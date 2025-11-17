#!/bin/bash
# Hammer test server with 100 extractall requests

BASE_URL="http://localhost:8000"
COOKIES="cookies.txt"

# Login first
echo "Logging in as test@example.com..."
# Get login page to get CSRF token
LOGIN_CSRF=$(curl -s -c $COOKIES "${BASE_URL}/" | grep -oP 'name="csrfmiddlewaretoken" value="\K[^"]+' | head -1)

# Login with test user
curl -s -b $COOKIES -c $COOKIES \
    -X POST \
    -d "csrfmiddlewaretoken=${LOGIN_CSRF}" \
    -d "user=test@example.com" \
    "${BASE_URL}/login/" \
    -L -o /dev/null

# Get CSRF token for extractall
echo "Getting CSRF token for submission..."
CSRF_TOKEN=$(curl -s -b $COOKIES "${BASE_URL}/extractall/" | grep -oP 'name="csrfmiddlewaretoken" value="\K[^"]+' | head -1)

if [ -z "$CSRF_TOKEN" ]; then
    echo "Failed to get CSRF token"
    exit 1
fi

echo "CSRF token: $CSRF_TOKEN"
echo "Submitting 100 requests..."

# Submit 100 requests in parallel
for i in {1..100}; do
    # Wavelength around 2000Å, 1Å interval
    START=$(echo "2000 + $i * 0.1" | bc)
    END=$(echo "$START + 1.0" | bc)

    curl -s -b $COOKIES \
        -X POST \
        -d "csrfmiddlewaretoken=${CSRF_TOKEN}" \
        -d "reqtype=extractall" \
        -d "stwvl=${START}" \
        -d "endwvl=${END}" \
        -d "format=short" \
        -d "viaftp=email" \
        -d "pconf=default" \
        "${BASE_URL}/submit/" \
        -o "response_${i}.html" &

    echo "Submitted request $i: ${START}-${END}Å"
    
    # Add small delay every 10 requests to avoid overwhelming
    if [ $((i % 10)) -eq 0 ]; then
        sleep 0.1
    fi
done

echo "Waiting for all requests to complete..."
wait

echo "Done! Checking results..."
SUCCESS=$(grep -l "successfully\|Request submitted" response_*.html 2>/dev/null | wc -l)
REDIRECTS=$(grep -l "request/" response_*.html 2>/dev/null | wc -l)
ERRORS=$(grep -l "error\|Error\|ERROR\|404" response_*.html 2>/dev/null | wc -l)
echo "Successful/Redirects: $REDIRECTS, Errors: $ERRORS"

# Check queue status
echo ""
echo "Checking Request database..."
