# Pinned vendored dependencies

Builds do not download dependencies. Original license files and SHA256SUMS
are included alongside each unmodified header.

| Library | Version | Source | License |
| --- | --- | --- | --- |
| nlohmann/json | 3.11.3 | https://github.com/nlohmann/json/tree/v3.11.3 | MIT, json.LICENSE |
| cpp-httplib | 0.18.3 | https://github.com/yhirose/cpp-httplib/tree/v0.18.3 | MIT, httplib.LICENSE |
| PicoSHA2 | 1.0.1 | https://github.com/okdshin/PicoSHA2/tree/v1.0.1 | MIT, picosha2.LICENSE |

HTTPS termination, if required, belongs to an external proxy. The controller
does not link OpenSSL or implement a TLS listener.
