from .app import create_app
app = create_app()

if __name__ == '__main__':
    import ipaddress
    import os
    import uvicorn
    settings = app.state.security_settings
    proxies = os.getenv('CQA_TRUSTED_PROXY_IPS', '127.0.0.1').split(',')
    for value in proxies:
        ipaddress.ip_address(value.strip())  # No wildcard/header-trust bypass.
    uvicorn.run(app, host=settings.bind_host, port=8000,
                proxy_headers=settings.mode == 'entra',
                forwarded_allow_ips=','.join(x.strip() for x in proxies))
