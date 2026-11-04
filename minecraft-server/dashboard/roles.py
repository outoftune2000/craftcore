"""Read-only player projections of administrative telemetry."""
PLAYER_READ_PATHS = frozenset({
    '/api/status','/api/me','/api/players/inventory','/api/map/data',
    '/api/map/terrain','/api/map/regions','/api/map/region_tile',
})

def player_metrics(data):
    fields = ('server_running','server_state','edition','software','version','server_cpu_percent',
              'server_ram_mb','server_uptime_seconds','system_cpu_percent','system_ram_used_mb',
              'system_ram_total_mb','system_ram_percent','disk_used_gb','disk_total_gb',
              'disk_percent','temperatures','tunnel_running','tunnel_address','online_players_count','port')
    result = {key:data[key] for key in fields if key in data}
    result['online_players'] = [{'name':p.get('name',p.get('username','Player'))} for p in data.get('online_players',[])]
    result['world'] = {key:data.get('world',{}).get(key) for key in ('exists','name','size_mb')}
    return result

def metrics_for(data, identity):
    return data if identity and identity.get('role')=='admin' else player_metrics(data)

def map_for(data, identity):
    if identity and identity.get('role')=='admin': return data
    return {**data,'players':[{key:p[key] for key in ('name','uuid','dimension','pos','is_online') if key in p}
                             for p in data.get('players',[])]}
