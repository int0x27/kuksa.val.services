#!/usr/bin/env python3
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

import getopt
import logging
import os
import sys

import grpc
import sdv.edge.comfort.trunk.v1.trunk_pb2 as pb2
import sdv.edge.comfort.trunk.v1.trunk_pb2_grpc as pb2_grpc
from sdv.edge.comfort.trunk.v1.trunk_pb2 import LockState, TrunkInstance

logger = logging.getLogger(__name__)


class TrunkTestClient(object):
    """
    Client for gRPC functionality
    """

    def __init__(self, trunk_addr: str):
        self._trunk_addr = trunk_addr
        logger.info("Connecting to trunk service %s", self._trunk_addr)

        # instantiate a channel
        self.channel = grpc.insecure_channel(self._trunk_addr)

        # bind the client and the server
        self.stub = pb2_grpc.TrunkStub(self.channel)

    def execute_methods(self, lock_state: LockState, open_state: bool) -> None:
        """
        Client function to call the rpc for TrunkService methods
        """
        logger.info("Setting Lock State: %s", self.get_trunk_str(lock_state))
        request = pb2.SetLockStateRequest(instance=TrunkInstance.REAR, state=lock_state)
        self.stub.SetLockState(request)

        if open_state:
            logger.info("Opening Trunk (if not already) ...")
            request = pb2.OpenRequest(instance=TrunkInstance.REAR)
            self.stub.Open(request)
        else:
            logger.info("Closing Trunk (if not already) ...")
            request = pb2.CloseRequest(instance=TrunkInstance.REAR)
            self.stub.Close(request)

        logger.info("Done.")

    def get_trunk_str(self, trunk_value) -> str:
        if trunk_value == 0:
            return "LockState.UNLOCKED"
        elif trunk_value == 1:
            return "LockState.LOCKED"
        else:
            return "Invalid value: {}".format(trunk_value)


def main(argv):
    """Main function"""

    default_addr = "127.0.0.1:50053"
    default_lockstate = "1"
    default_openstate = "0"

    _usage = (
        "Usage: ./testclient.py --addr <host:name>"  # shorten line
        " --lockstate=LOCK_STATE --openstate=OPEN_STATE\n\n"
        "Environment:\n"
        "  'VDB_ADDR'    Databroker address (host:port). Default: {}\n"
        "  'LOCK_STATE'  Trunk Lock State (0=unlocked, 1=locked). Default: {}\n"
        "  'OPEN_STATE'  Trunk Open State (0=closed, 1=open). Default: {}\n".format(
            default_addr, default_lockstate, default_openstate
        )
    )

    # environment values (overridden by cmdargs)
    trunk_addr = os.getenv("TRUNK_ADDR", default_addr)
    lock_state = LockState.LOCKED if os.getenv("LOCK_STATE") != "0" else LockState.UNLOCKED
    open_state = True if os.getenv("OPEN_STATE") != "0" else False

    # parse cmdline args
    try:
        opts, args = getopt.getopt(argv, "ha:l:o:", ["addr=", "lock=", "open="])
        for opt, arg in opts:
            if opt == "-h":
                print(_usage)
                sys.exit(0)
            elif opt in ("-a", "--addr"):
                trunk_addr = arg
            elif opt in ("-l", "--lock"):
                lock_state = LockState.LOCKED if arg != "0" else LockState.UNLOCKED
            elif opt in ("-o", "--open"):
                open_state = True if arg != "0" else False
            else:
                print("Unknown arg: {}".format(opt))
                print(_usage)
                sys.exit(1)
    except getopt.GetoptError:
        print(_usage)
        sys.exit(1)

    client = TrunkTestClient(trunk_addr)
    client.execute_methods(lock_state, open_state)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(sys.argv[1:])
