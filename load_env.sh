#!/bin/bash
for i in $(cat backend/.env) ; do $(echo "export $i"); done

