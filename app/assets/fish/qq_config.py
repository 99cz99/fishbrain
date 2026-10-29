"""Migrate only the Fishbrain OneBot connection, retaining user credentials."""
import copy


def delivery_config(data, template):
    result = copy.deepcopy(data)
    servers = result.setdefault('network', {}).setdefault('websocketServers', [])
    found = False
    for server in servers:
        if isinstance(server,dict) and server.get('port') == 8081 and server.get('host') in ('127.0.0.1','localhost','',None):
            server.update(reportSelfMessage=True,enableForcePushEvent=True,messagePostFormat='array')
            found = True
    if not found:
        servers.extend(copy.deepcopy(template['network']['websocketServers']))
    timeout = result.setdefault('timeout', {})
    timeout['baseTimeout'] = max(30000,int(timeout.get('baseTimeout',10000)))
    return result
