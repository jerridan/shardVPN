# Based on the great work completed by Kyle Manna: https://github.com/kylemanna/docker-openvpn

FROM alpine:latest

RUN apk add --update bash easy-rsa openvpn python py-pip && \
    pip install awscli && \
    ln -s /usr/share/easy-rsa/easyrsa /usr/local/bin && \
    rm -rf /tmp/* /var/tmp/* /var/cache/apk/* /var/cache/distfiles/*

ENV OPENVPN /etc/openvpn
ENV OVPN_ENV $OPENVPN/ovpn_env.sh
ENV SHARD_VPN_VOLUME /etc/shardvpn
ENV CLIENTNAME "shard-vpn-client"
ENV SERVERNAME "shard-vpn-drive"

RUN ["mkdir", "-p", "/etc/openvpn/server"]
RUN ["mkdir", "-p", "/etc/openvpn/client"]
RUN ["mkdir", "/etc/shardvpn"]

ADD scripts /usr/local/bin
RUN chmod a+x /usr/local/bin/*

WORKDIR $OPENVPN

CMD ["drive.sh"]
