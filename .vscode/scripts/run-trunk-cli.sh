#!/bin/bash
#********************************************************************************
# Copyright (c) 2022 Contributors to the Eclipse Foundation
#
# See the NOTICE file(s) distributed with this work for additional
# information regarding copyright ownership.
#
# This program and the accompanying materials are made available under the
# terms of the Apache License 2.0 which is available at
# http://www.apache.org/licenses/LICENSE-2.0
#
# SPDX-License-Identifier: Apache-2.0
#*******************************************************************************/
# shellcheck disable=SC2034
# shellcheck disable=SC2086

echo "#######################################################"
echo "### Running Trunk Client                            ###"
echo "#######################################################"

set -e

ROOT_DIRECTORY=$(git rev-parse --show-toplevel)
# shellcheck source=/dev/null
source "$ROOT_DIRECTORY/.vscode/scripts/exec-check.sh" "$@"

[ "$1" = "--task" ] && shift

LOCK="$1"
OPEN="$2"

# sanity checks for invalid user input
if [ -z "$LOCK" ] || [ -z "$LOCK" ]; then
	echo "Invalid arguments!"
	echo
	echo "Usage: $0 <LOCK_STATUS> <OPEN_STATUS>"
	echo
	exit 1
fi

TRUNKSERVICE_PORT='50053'
TRUNKSERVICE_EXEC_PATH="$ROOT_DIRECTORY/trunk_service"
if [ ! -f "$TRUNKSERVICE_EXEC_PATH/testclient.py" ]; then
	echo "Can't find $TRUNKSERVICE_EXEC_PATH/testclient.py"
	exit 1
fi

cd "$TRUNKSERVICE_EXEC_PATH" || exit 1
pip3 install -q -r requirements.txt

set -x
python3 -u testclient.py --addr=localhost:$TRUNKSERVICE_PORT --lock=$LOCK --open=$OPEN
