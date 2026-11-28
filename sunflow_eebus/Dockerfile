# The Supervisor builds apps with this folder as context, so the code is cloned from GitHub.
# BUILD_VERSION (set by the Supervisor from config.yaml) invalidates the cached clone.
FROM golang:1.24-alpine AS build
ARG BUILD_VERSION
ARG SUNFLOW_REPO=https://github.com/Zwer2k/sunflow
ARG SUNFLOW_REF=main
RUN apk add --no-cache git \
    && echo "Sunflow EEBUS ${BUILD_VERSION}" \
    && git clone --depth 1 --branch "${SUNFLOW_REF}" "${SUNFLOW_REPO}" /src
WORKDIR /src/eebus
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${BUILD_VERSION}" -o /sunflow-eebus .

FROM alpine:3.20
COPY --from=build /sunflow-eebus /usr/local/bin/sunflow-eebus
# The program carries the code of eebus-go and its modules: their licences travel with it
COPY --from=build /src/LICENSE /src/THIRD_PARTY_NOTICES.md /src/eebus/THIRD_PARTY_LICENSES.txt /usr/share/doc/sunflow-eebus/
CMD ["/usr/local/bin/sunflow-eebus"]
