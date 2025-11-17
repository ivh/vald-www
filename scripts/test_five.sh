#!/bin/bash
# Quick test with 5 requests

BASE_URL="http://localhost:8000"
COOKIES="cookies_test.txt"

# Login
echo "Logging in..."
LOGIN_CSRF=$(curl -s -c $COOKIES "${BASE_URL}/" | grep -oP 'name="csrfmiddlewaretoken" value="\K[^"]+' | head -1)
curl -s -b $COOKIES -c $COOKIES -X POST -d "csrfmiddlewaretoken=${LOGIN_CSRF}" -d "user=test@example.com" "${BASE_URL}/login/" -L -o /dev/null

# Get CSRF token
CSRF_TOKEN=$(curl -s -b $COOKIES "${BASE_URL}/extractall/" | grep -oP 'name="csrfmiddlewaretoken" value="\K[^"]+' | head -1)

echo "Submitting 5 test requests..."
for i in {1..5}; do
    START=$(echo "2100 + $i * 0.1" | bc)
    END=$(echo "$START + 1.0" | bc)
    
    curl -s -b $COOKIES -X POST \
        -d "csrfmiddlewaretoken=${CSRF_TOKEN}" \
        -d "reqtype=extractall" \
        -d "stwvl=${START}" \
        -d "endwvl=${END}" \
        -d "format=short" \
        -d "viaftp=email" \
        -d "pconf=default" \
        "${BASE_URL}/submit/" \
        -o /dev/null &
    
    echo "Submitted request $i: ${START}-${END}Å"
done

wait
rm $COOKIES
echo "All requests submitted. Checking status in 5 seconds..."
