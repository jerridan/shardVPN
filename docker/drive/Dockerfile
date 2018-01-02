# Based on the great work completed by Kyle Manna: https://github.com/kylemanna/docker-openvpn

FROM alpine:latest

RUN apk add --update bash easy-rsa openvpn && \
    ln -s /usr/share/easy-rsa/easyrsa /usr/local/bin && \
    rm -rf /tmp/* /var/tmp/* /var/cache/apk/* /var/cache/distfiles/*

ENV OPENVPN /etc/openvpn
ENV OVPN_ENV $OPENVPN/ovpn_env.sh
ENV EASYRSA /usr/share/easy-rsa
ENV EASYRSA_PKI $OPENVPN/pki
ENV BLINK /etc/blinkvpn
ENV CLIENTNAME "blink-client"
ENV SERVERNAME "blink-drive"

RUN ["mkdir", "-p", "/etc/openvpn/server"]
RUN ["mkdir", "-p", "/etc/openvpn/client"]

VOLUME ["/etc/blinkvpn"]

# To be removed when the certifier is linked in with the drive
ADD ./test_data /etc/openvpn/test_data

ADD ./scripts /usr/local/bin
RUN chmod a+x /usr/local/bin/*

WORKDIR $EASYRSA

CMD ["drive.sh"]
