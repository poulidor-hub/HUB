# Résolveur DNS du hub (chantier B — routage par noms)
# dnsmasq du dépôt Alpine (2.90) — configuration montée dans /etc/dnsmasq.d
# (dns/dnsmasq.conf + hub.zone générés par hub-api, cf. docs/DOCKER_SOCKET_PROXY.md
#  et docs/ROUTAGE-DNS.md).
FROM alpine:3.20

RUN apk add --no-cache dnsmasq

# Port 53 : bindé via network_mode host (compose) — NET_BIND_SERVICE requis.
EXPOSE 53/udp 53/tcp

CMD ["dnsmasq", "-C", "/etc/dnsmasq.d/dnsmasq.conf", "--no-daemon"]
