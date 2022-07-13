# /********************************************************************************
# * Copyright (c) 2022 Contributors to the Eclipse Foundation
# *
# * See the NOTICE file(s) distributed with this work for additional
# * information regarding copyright ownership.
# *
# * This program and the accompanying materials are made available under the
# * terms of the Apache License 2.0 which is available at
# * http://www.apache.org/licenses/LICENSE-2.0
# *
# * SPDX-License-Identifier: Apache-2.0
# ********************************************************************************/

import asyncio
import logging
import os
import random
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

import grpc
from sdv.databroker.v1.collector_pb2 import (
    RegisterDatapointsRequest,
    RegistrationMetadata,
    UpdateDatapointsRequest,
)
from sdv.databroker.v1.collector_pb2_grpc import CollectorStub
from sdv.databroker.v1.types_pb2 import ChangeType, DataType
from sdv.edge.comfort.trunk.v1.trunk_pb2 import (
    CloseReply,
    CloseRequest,
    LockState,
    OpenReply,
    OpenRequest,
    SetLockStateReply,
    SetLockStateRequest,
    TrunkInstance,
)
from sdv.edge.comfort.trunk.v1.trunk_pb2_grpc import (
    TrunkServicer,
    add_TrunkServicer_to_server,
)

log = logging.getLogger("trunk_service")
event = threading.Event()

# Trunk Service bind "host:port"
TRUNK_ADDRESS = os.getenv("TRUNK_ADDR", "0.0.0.0:50053")
# VehicleDataBroker address, overridden if "DAPR_GRPC_PORT" is set in environment
VDB_ADDRESS = os.getenv("VDB_ADDRESS", "127.0.0.1:55555")

def init_logging(loglevel):
    # create console handler and set level to debug
    console_logger = logging.StreamHandler()
    console_logger.setLevel(logging.DEBUG)

    # create formatter
    if sys.stdout.isatty():
        formatter = ColorFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

    # add formatter to console_logger
    console_logger.setFormatter(formatter)

    # add console_logger as a global handler
    root_logger = logging.getLogger()
    root_logger.setLevel(loglevel)
    root_logger.addHandler(console_logger)


class ColorFormatter(logging.Formatter):
    FORMAT = "{time} {{loglevel}} {logger} {msg}".format(
        time="\x1b[2m%(asctime)s\x1b[0m",  # grey
        logger="\x1b[2m%(name)s:\x1b[0m",  # grey
        msg="%(message)s",
    )
    FORMATS = {
        logging.DEBUG: FORMAT.format(loglevel="\x1b[34mDEBUG\x1b[0m"),  # blue
        logging.INFO: FORMAT.format(loglevel="\x1b[32mINFO \x1b[0m"),  # green
        logging.WARNING: FORMAT.format(loglevel="\x1b[33mWARNING\x1b[0m"),  # yellow
        logging.ERROR: FORMAT.format(loglevel="\x1b[31mERROR\x1b[0m"),  # red
        logging.CRITICAL: FORMAT.format(loglevel="\x1b[31mCRITICAL\x1b[0m"),  # red
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)


def is_grpc_fatal_error(e: grpc.RpcError) -> bool:
    if (
        e.code() == grpc.StatusCode.UNAVAILABLE
        or e.code() == grpc.StatusCode.UNKNOWN
        or e.code() == grpc.StatusCode.UNAUTHENTICATED
        or e.code() == grpc.StatusCode.INTERNAL
    ):
        log.error("Feeding aborted due to RpcError(%s, '%s')", e.code(), e.details())
        return True
    else:
        log.warning("Unhandled RpcError(%s, '%s')", e.code(), e.details())
        return False


class TrunkService:
    """API to access signals."""

    def __init__(self, trunk_address: str):
        if os.getenv("DAPR_GRPC_PORT") is not None:
            grpc_port = int(os.getenv("DAPR_GRPC_PORT"))
            self._vdb_address = f"127.0.0.1:{grpc_port}"
        else:
            self._vdb_address = VDB_ADDRESS
        self._address = trunk_address
        self._ids = {}
        self._connected = False
        
        # initial gps position
        self._location = {"lat": 52.15034564571311, "lon": .93070999496221, "h_acc": 3, "v_acc": 2}
        
        self._registered = False
        self._shutdown = False
        self._databroker_thread = Thread(
            target=self.connect_to_databroker, daemon=True, name="databroker-connector"
        )
        self._databroker_thread.start()
        # self.connect_to_databroker()

    def connect_to_databroker(self) -> None:
        log.info("Connecting to Data Broker [%s]", self._vdb_address)
        if os.getenv("VEHICLEDATABROKER_DAPR_APP_ID") is not None:
            self._metadata = (
                ("dapr-app-id", os.getenv("VEHICLEDATABROKER_DAPR_APP_ID")),
            )
            # give some time for dapr sidecar startup...
            time.sleep(2)
        else:
            self._metadata = None
        self._channel: grpc.Channel = grpc.insecure_channel(self._vdb_address)
        self._stub = CollectorStub(self._channel)
        log.info("Using gRPC metadata: %s", self._metadata)
        self._channel.subscribe(
            lambda connectivity: self.on_broker_connectivity_change(connectivity),
            try_to_connect=False,
        )
        self._run()

    def on_broker_connectivity_change(self, connectivity):
        log.info("[%s] Connectivity changed to: %s", self._vdb_address, connectivity)
        if (
            connectivity == grpc.ChannelConnectivity.READY
            or connectivity == grpc.ChannelConnectivity.IDLE
        ):
            # Can change between READY and IDLE. Only act if coming from
            # unconnected state
            if not self._connected:
                log.info("Connected to data broker")
                try:
                    self.register_datapoints()
                    log.info("datapoints are registered.")
                    self._registered = True
                    # Setting a dummy position for Hackathon 2 "Reunion" once after successful registration
                    self.set_dummy_location()
                except grpc.RpcError as err:
                    log.error("Failed to register datapoints")
                    is_grpc_fatal_error(err)
                    # log.error("Failed to register datapoints", exc_info=True)
                except Exception:
                    log.error("Failed to register datapoints", exc_info=True)
                self._connected = True
        else:
            if self._connected:
                log.info("Disconnected from data broker")
            else:
                if connectivity == grpc.ChannelConnectivity.CONNECTING:
                    log.info("Trying to connect to data broker")
            self._connected = False
            self._registered = False

    def _run(self):
        while self._shutdown is False:
            if not self._connected:
                time.sleep(0.2)
                continue
            elif not self._registered:
                try:
                    log.debug("Try to register datapoints")
                    self.register_datapoints()
                    self._registered = True
                except grpc.RpcError as err:
                    is_grpc_fatal_error(err)
                    log.debug("Failed to register datapoints", exc_info=True)
                    time.sleep(3)
                except Exception:
                    log.error("Failed to register datapoints", exc_info=True)
                    time.sleep(10)
                    continue
            else:
                # feed some dummy location
                self.set_dummy_location()
                # TODO: check if dapr grpc proxy has active connection
                time.sleep(10)

    def serve(self):
        log.info("Starting Trunk Service on %s", self._address)
        server = grpc.server(ThreadPoolExecutor(max_workers=10))
        _servicer = self._TrunkService(self)
        add_TrunkServicer_to_server(_servicer, server)
        server.add_insecure_port(self._address)
        server.start()
        server.wait_for_termination()

    async def close(self):
        """Closes runtime gRPC channel."""
        if self._channel:
            await self._channel.close()

    def __enter__(self) -> "TrunkService":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        asyncio.run_coroutine_threadsafe(self.close(), asyncio.get_event_loop())

    def register_datapoints(self):
        log.info("Try register datapoints")
        self.register(
            "Vehicle.Body.Trunk.Front.IsOpen",
            DataType.BOOL,
            ChangeType.ON_CHANGE,
        )
        self.register(
            "Vehicle.Body.Trunk.Front.IsLocked",
            DataType.BOOL,
            ChangeType.ON_CHANGE,
        )
        self.register(
            "Vehicle.Body.Trunk.Rear.IsOpen",
            DataType.BOOL,
            ChangeType.ON_CHANGE,
        )
        self.register(
            "Vehicle.Body.Trunk.Rear.IsLocked",
            DataType.BOOL,
            ChangeType.ON_CHANGE,
        )

        # Basically not part of this service, but served here for Hackathon 2 "Reunion" purposes:
        self.register(
            "Vehicle.CurrentLocation.Latitude",
            DataType.DOUBLE,
            ChangeType.ON_CHANGE,
        )
        self.register(
            "Vehicle.CurrentLocation.Longitude",
            DataType.DOUBLE,
            ChangeType.ON_CHANGE,
        )
        self.register(
            "Vehicle.CurrentLocation.HorizontalAccuracy",
            DataType.DOUBLE,
            ChangeType.ON_CHANGE,
        )
        self.register(
            "Vehicle.CurrentLocation.VerticalAccuracy",
            DataType.DOUBLE,
            ChangeType.ON_CHANGE,
        )

    def register(self, name, data_type, change_type):
        self._register(name, data_type, change_type)

    def _register(self, name, data_type, change_type):
        request = RegisterDatapointsRequest()
        registration_metadata = RegistrationMetadata()
        registration_metadata.name = name
        registration_metadata.data_type = data_type
        registration_metadata.description = ""
        registration_metadata.change_type = change_type
        request.list.append(registration_metadata)
        response = self._stub.RegisterDatapoints(request, metadata=self._metadata)
        self._ids[name] = response.results[name]
        log.info("Registered %s with id: %s", name, self._ids[name])

    def simulate_location(self):
        # simulate some location jitter
        self._location["lat"] = self._location["lat"] + random.uniform(  #nosec B313
            -0.00001, 0.00001
        )
        self._location["lat"] = self._location["lat"] + random.uniform(  #nosec B313
            -0.00001, 0.00001
        )
        self._location["lon"] = self._location["lon"] + random.uniform(  #nosec B313
            -0.00001, 0.00001
        )
        self._location["h_acc"] = random.randint(1, 10)  # nosec
        self._location["v_acc"] = random.randint(1, 20)  # nosec
        
    
    def set_dummy_location(self):
        self.simulate_location()
        request = UpdateDatapointsRequest()
        request.datapoints[
            self._ids["Vehicle.CurrentLocation.Latitude"]
        ].double_value = self._location["lat"]
        request.datapoints[
            self._ids["Vehicle.CurrentLocation.Longitude"]
        ].double_value = self._location["lon"]
        request.datapoints[
            self._ids["Vehicle.CurrentLocation.HorizontalAccuracy"]
        ].double_value = self._location["h_acc"]
        request.datapoints[
            self._ids["Vehicle.CurrentLocation.VerticalAccuracy"]
        ].double_value = self._location["v_acc"]

        try:
            log.debug(" Feeding location: %s", self._location);
            self._stub.UpdateDatapoints(request, metadata=self._metadata)
        except grpc.RpcError as err:
            log.warning("Feeding of current dummy location failed", exc_info=True)
            self._connected = is_grpc_fatal_error(err)
            raise err

    def set_bool_datapoint(self, name: str, value: bool):
        if self._connected and self._registered:
            id = self._ids[name]
            request = UpdateDatapointsRequest()
            request.datapoints[id].bool_value = value
            log.info(" Feeding '%s' with value %s", name, value)
            try:
                self._stub.UpdateDatapoints(request, metadata=self._metadata)
            except grpc.RpcError as err:
                log.warning("Feeding %s failed", name, exc_info=True)
                self._connected = is_grpc_fatal_error(err)
                raise err
        else:
            log.warning(
                "Ignore updating datapoints as data broker isn't available or datapoints not registered"
            )

    class _TrunkService(TrunkServicer):
        def __init__(self, servicer):
            self.servicer: TrunkService = servicer

        def SetLockState(self, request: SetLockStateRequest, context):
            log.info(
                "* Request to set lock state of %s", str(request).replace("\n", " ")
            )
            if (
                request.instance == TrunkInstance.ALL
                or request.instance == TrunkInstance.FRONT
            ):
                self.servicer.set_bool_datapoint(
                    "Vehicle.Body.Trunk.Front.IsLocked",
                    (request.state == LockState.LOCKED),
                )
            if (
                request.instance == TrunkInstance.ALL
                or request.instance == TrunkInstance.REAR
            ):
                self.servicer.set_bool_datapoint(
                    "Vehicle.Body.Trunk.Rear.IsLocked",
                    (request.state == LockState.LOCKED),
                )
            log.info(" Lock state updated.\n")
            return SetLockStateReply()

        def Open(self, request: OpenRequest, context):
            log.info("* Request to open %s", str(request).replace("\n", " "))
            time.sleep(0.1)
            if (
                request.instance == TrunkInstance.ALL
                or request.instance == TrunkInstance.FRONT
            ):
                self.servicer.set_bool_datapoint(
                    "Vehicle.Body.Trunk.Front.IsOpen", True
                )
            if (
                request.instance == TrunkInstance.ALL
                or request.instance == TrunkInstance.REAR
            ):
                self.servicer.set_bool_datapoint("Vehicle.Body.Trunk.Rear.IsOpen", True)
            log.info(" Opening trunk.\n")
            return OpenReply()

        def Close(self, request: CloseRequest, context):
            log.info("* Request to close %s", str(request).replace("\n", " "))
            time.sleep(2)
            if (
                request.instance == TrunkInstance.ALL
                or request.instance == TrunkInstance.FRONT
            ):
                self.servicer.set_bool_datapoint(
                    "Vehicle.Body.Trunk.Front.IsOpen", False
                )
            if (
                request.instance == TrunkInstance.ALL
                or request.instance == TrunkInstance.REAR
            ):
                self.servicer.set_bool_datapoint(
                    "Vehicle.Body.Trunk.Rear.IsOpen", False
                )
            log.info(" Closed trunk.\n")
            return CloseReply()


async def main():
    """Main function"""
    trunk_service = TrunkService(TRUNK_ADDRESS)
    trunk_service.serve()


if __name__ == "__main__":
        # set root loglevel etc
    init_logging(logging.INFO)
    # logging.basicConfig(level=logging.INFO)
    log.setLevel(logging.DEBUG)
    LOOP = asyncio.get_event_loop()
    LOOP.add_signal_handler(signal.SIGTERM, LOOP.stop)
    LOOP.run_until_complete(main())
    LOOP.close()
