#!/usr/bin/env bash

VERSION=$(sentry-cli releases propose-version || exit)

#cd aztec || exit
#mvn clean package || exit
#cd ..

docker buildx build --platform linux/amd64 --push -t "theenbyperor/vdv-pkpass-django:$VERSION" . || exit