#!/bin/bash
BASE_URL="http://localhost:8000"
COOKIES="cookies_one.txt"

LOGIN_CSRF=$(curl -s -c $COOKIES "${BASE_URL}/" | grep -oP 'name="csrfmiddlewaretoken" value="\K[^"]+' | head -1)
curl -s -b $COOKIES -c $COOKIES -X POST -d "csrfmiddlewaretoken=${LOGIN_CSRF}" -d "user=test@example.com" "${BASE_URL}/login/" -L -o /dev/null

CSRF_TOKEN=$(curl -s -b $COOKIES "${BASE_URL}/extractall/" | grep -oP 'name="csrfmiddlewaretoken" value="\K[^"]+' | head -1)

echo "Submitting test request..."
curl -s -b $COOKIES -X POST \
    -d "csrfmiddlewaretoken=${CSRF_TOKEN}" \
    -d "reqtype=extractall" \
    -d "stwvl=2400.0" \
    -d "endwvl=2401.0" \
    -d "format=short" \
    -d "viaftp=email" \
    -d "pconf=default" \
    "${BASE_URL}/submit/" \
    -o response_one.html

rm $COOKIES
echo "Done. Response saved to response_one.html"
