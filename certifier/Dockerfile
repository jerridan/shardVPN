# Based on the great work completed by Kyle Manna: https://github.com/kylemanna/docker-openvpn

FROM alpine:latest

RUN apk add --update bash curl easy-rsa openvpn && \
    ln -s /usr/share/easy-rsa/easyrsa /usr/local/bin && \
    rm -rf /tmp/* /var/tmp/* /var/cache/apk/* /var/cache/distfiles/*

ENV OPENVPN /etc/openvpn
ENV EASYRSA /usr/share/easy-rsa
ENV EASYRSA_PKI $OPENVPN/pki
ENV BLINK_KEYS /etc/blinkvpn
ENV DOWNLOADS /etc/downloads
ENV TERRAFORM /etc/terraform
ENV CLIENTNAME "blink-client"
ENV SERVERNAME "blink-drive"

RUN ["mkdir", "/etc/blinkvpn"]
RUN ["mkdir", "/etc/downloads"]
RUN ["mkdir", "/etc/terraform"]

ADD scripts "/usr/local/bin"
ADD terraform "/etc/terraform"
RUN chmod a+wx /usr/local/bin/*

WORKDIR $EASYRSA

RUN ["install_terraform.sh"]
CMD ["certifier.sh"]
