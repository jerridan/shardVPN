# Based on the great work completed by Kyle Manna: https://github.com/kylemanna/docker-openvpn

FROM alpine:latest

RUN apk add --update bash easy-rsa && \
    ln -s /usr/share/easy-rsa/easyrsa /usr/local/bin && \
    rm -rf /tmp/* /var/tmp/* /var/cache/apk/* /var/cache/distfiles/*

ENV OPENVPN /etc/openvpn
ENV EASYRSA /usr/share/easy-rsa
ENV EASYRSA_PKI $OPENVPN/pki
ENV BLINK /etc/blinkvpn

VOLUME ["/etc/blinkvpn"]

ADD ./scripts/* /usr/local/bin
RUN chmod a+x /usr/local/bin/*

WORKDIR $EASYRSA

CMD ["drive.sh"]
