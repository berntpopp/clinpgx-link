#!/usr/bin/env bash
# Reproduces the network timing protocol used on 2026-09-05.
# Original environment:
#   Ubuntu 26.04.1 LTS, Linux 7.0.0-30-generic x86_64
#   curl 8.18.0, libcurl 8.18.0, OpenSSL 3.6.2, nghttp2 1.67.0
# Run from any directory; writes TSV measurements to stdout and bodies to /dev/null.
# The persistent section is explicitly limited to one request start per second.
set -euo pipefail

api_base='https://api.clinpgx.org/v1'
write_out='%{http_code}\t%{time_namelookup}\t%{time_connect}\t%{time_starttransfer}\t%{time_total}\t%{size_download}\t%{content_type}\t%{num_connects}\t%{url_effective}\n'

printf 'mode\tresource\trun\thttp_status\tdns_s\tconnect_s\tttfb_s\ttotal_s\tbytes\tcontent_type\tnew_connections\teffective_url\n'

new_probe() {
  local resource="$1"
  local run="$2"
  local url="$3"
  printf 'new_connection\t%s\t%s\t' "$resource" "$run"
  curl --fail --silent --show-error --http2 --max-time 60 \
    --write-out "$write_out" --output /dev/null "$url"
  sleep 0.6
}

for run in 1 2 3; do
  new_probe gene_cyp2c19_max "$run" "$api_base/data/gene/PA124?view=max"
  new_probe chemical_clopidogrel_max "$run" "$api_base/data/chemical/PA449053?view=max"
  new_probe variant_rs4244285_max "$run" "$api_base/data/variant/PA166154053?view=max"
  new_probe summary_clopidogrel_cyp2c19_max "$run" "$api_base/data/summaryAnnotation/655386913?view=max"
  new_probe guideline_clopidogrel_cyp2c19_max "$run" "$api_base/data/guidelineAnnotation/PA166104948?view=max"
done

# A single curl process preserves the HTTP/2 connection. --rate 1/s enforces a
# stricter request-start rate than ClinPGx's published two requests/second.
persistent_args=()
for run in 1 2 3; do
  persistent_args+=(
    --output /dev/null "$api_base/data/gene/PA124?view=max"
    --output /dev/null "$api_base/data/chemical/PA449053?view=max"
    --output /dev/null "$api_base/data/variant/PA166154053?view=max"
    --output /dev/null "$api_base/data/summaryAnnotation/655386913?view=max"
    --output /dev/null "$api_base/data/guidelineAnnotation/PA166104948?view=max"
  )
done
curl --fail --silent --show-error --http2 --rate 1/s --max-time 60 \
  --write-out "%{urlnum}\t${write_out}" \
  "${persistent_args[@]}" |
  awk -F '\t' 'BEGIN {
    OFS = "\t"
    resource[0] = "gene_cyp2c19_max"
    resource[1] = "chemical_clopidogrel_max"
    resource[2] = "variant_rs4244285_max"
    resource[3] = "summary_clopidogrel_cyp2c19_max"
    resource[4] = "guideline_clopidogrel_cyp2c19_max"
  }
  {
    idx = $1
    run = int(idx / 5) + 1
    print "persistent_http2", resource[idx % 5], run, $2, $3, $4, $5, $6, $7, $8, $9, $10
  }'

for run in 1 2 3; do
  for name in genes.zip chemicals.zip drugs.zip summaryAnnotations.zip guidelineAnnotations.json.zip; do
    printf 'redirect_download\t%s\t%s\t' "$name" "$run"
    curl --fail --location --silent --show-error --http2 --max-time 90 \
      --write-out "$write_out" --output /dev/null \
      "$api_base/download/file/data/$name"
    sleep 0.6
  done
done
