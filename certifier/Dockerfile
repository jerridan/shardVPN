# Based on the great work completed by Kyle Manna: https://github.com/kylemanna/docker-openvpn

FROM alpine:latest

RUN apk add --no-cache --update bash easy-rsa openvpn && \
    ln -s /usr/share/easy-rsa/easyrsa /usr/local/bin && \
    rm -rf /tmp/* /var/tmp/* /var/cache/apk/* /var/cache/distfiles/*

ENV OPENVPN /etc/openvpn
ENV EASYRSA /usr/share/easy-rsa
ENV EASYRSA_PKI $OPENVPN/pki
ENV SHARD_VPN_KEYS /etc/shardvpn
ENV DOWNLOADS /etc/downloads
ENV CLIENTNAME "shard-vpn-client"
ENV SERVERNAME "shard-vpn-drive"

RUN ["mkdir", "/etc/shardvpn"]
RUN ["mkdir", "/etc/downloads"]

ADD scripts "/usr/local/bin"
RUN chmod a+wx /usr/local/bin/*

WORKDIR $EASYRSA

CMD ["certifier.sh"]
