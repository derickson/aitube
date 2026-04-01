#!/usr/bin/env bash
set -euo pipefail

BASE_URL="https://azathought.com/aitube/api"
AUTH_USER="tempuser"
AUTH_PASS="temppass"

usage() {
    echo "Usage: $0 <command> <youtube_url>"
    echo ""
    echo "Commands:"
    echo "  add     Submit a YouTube video for processing"
    echo "  delete  Delete a YouTube video by URL"
    echo ""
    echo "Examples:"
    echo "  $0 add https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    echo "  $0 delete https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    exit 1
}

extract_video_id() {
    echo "$1" | grep -oP '(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})' | head -1 | grep -oP '[a-zA-Z0-9_-]{11}$'
}

add_video() {
    local url="$1"
    echo "Submitting: $url"
    curl -s --digest -u "${AUTH_USER}:${AUTH_PASS}" \
        -X POST "${BASE_URL}/submit_video/" \
        -H "Content-Type: application/json" \
        -d "{\"urls\": [\"${url}\"]}" | python3 -m json.tool
}

delete_video() {
    local url="$1"
    local video_id
    video_id=$(extract_video_id "$url")

    if [[ -z "$video_id" ]]; then
        echo "Error: could not extract video ID from $url" >&2
        exit 1
    fi

    local external_id="yt_${video_id}"
    echo "Deleting ${external_id}..."
    curl -s --digest -u "${AUTH_USER}:${AUTH_PASS}" \
        -X DELETE "${BASE_URL}/content/by-external-id/${external_id}/" | python3 -m json.tool
}

[[ $# -lt 2 ]] && usage

case "$1" in
    add)    add_video "$2" ;;
    delete) delete_video "$2" ;;
    *)      usage ;;
esac
