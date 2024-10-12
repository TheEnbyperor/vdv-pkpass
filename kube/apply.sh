#!/usr/bin/env bash

VERSION=$(sentry-cli releases propose-version || exit)

sed -e "s/(version)/$VERSION/g" < django.yaml | kubectl apply -f - || exit
sed -e "s/(version)/$VERSION/g" < cron.yaml | kubectl apply -f - || exit
kubectl apply -f ingress.yaml || exit