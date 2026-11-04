const $ = id => document.getElementById(id);
let auth, status, versions = [], nextOffset = null, currentStep = 1, poller;
const fields = ['version','java','name','memory_mb','gamemode','difficulty','max_players','proxy_port','server_port','online_mode','eula'];
function error(message) { $('error').hidden = !message; $('error').textContent = message || ''; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if (!response.ok) {
    const text = await response.text();
    const parsed = new DOMParser().parseFromString(text,'text/html');
    throw new Error(parsed.querySelector('title')?.textContent || `Request failed (${response.status})`);
  }
  return response.json();
}
function data() {
  const result = {};
  for (const id of fields) result[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
  for (const id of ['memory_mb','max_players','proxy_port','server_port']) result[id] = Number(result[id]);
  return {...result, snapshot_confirm:$('snapshot-confirm').checked,offline_confirm:$('offline-confirm').checked,step:currentStep};
}
async function persist() { await api('/api/setup/draft',data()); }
function show(id, step) {
  for (const name of ['access','minecraft','configure','review','done']) $(name).hidden = name !== id;
  currentStep = step;
  [...$('steps').children].forEach((li,i) => li.classList.toggle('active',i+1===step));
  error('');
}
function versionChanged() {
  const item = versions.find(v => v.id === $('version').value);
  $('java-required').textContent = item ? `${item.id} requires Java ${item.java_major} or newer.` : 'Choose an available version.';
  $('snapshot-label').hidden = !item || item.type === 'release';
  $('snapshot-confirm').checked = false;
}
async function loadVersions(refresh=false, older=false) {
  $('refresh').disabled = true; $('older').disabled = true;
  $('catalog-status').textContent = 'Checking official server downloads…';
  try {
    const selected = $('version').value || status?.draft?.version || status?.settings?.version;
    const result = await api(`/api/versions?snapshots=${$('snapshots').checked?1:0}&refresh=${refresh?1:0}&offset=${older?nextOffset:0}`);
    versions = older ? [...versions,...result.versions] : result.versions;
    $('version').replaceChildren(...versions.map(v => { const o=document.createElement('option');o.value=v.id;o.textContent=`${v.id}${v.type==='snapshot'?' · snapshot':''}`;return o; }));
    if (versions.some(v=>v.id===selected)) $('version').value=selected;
    else if (versions.some(v=>v.id===result.latest.release)) $('version').value=result.latest.release;
    nextOffset=result.next_offset; $('older').hidden=nextOffset===null;
    $('catalog-status').textContent=`${result.stale?'Offline cache':'Catalog updated'} · ${new Date(result.fetched_at*1000).toLocaleString()}`;
    versionChanged();
  } catch(e) { error(e.message); $('catalog-status').textContent='Unable to load versions. Retry Refresh, or import the existing server.'; }
  finally { $('refresh').disabled=false;$('older').disabled=false; }
}
function guarded(fn) { return async event => { event?.preventDefault();error('');try{await fn();}catch(e){error(e.message);} }; }
$('access-form').onsubmit=guarded(async()=>{
  await api(auth.admin_exists?'/api/auth/login':'/api/setup/admin',{token:$('token').value,username:$('username').value,password:$('password').value});
  $('password').value='';$('token').value='';location.assign(auth.complete?'/':'/setup');
});
$('refresh').onclick=()=>loadVersions(true);
$('older').onclick=()=>loadVersions(false,true);
$('snapshots').onchange=()=>loadVersions();
$('version').onchange=versionChanged;
$('online_mode').onchange=()=>{$('offline-label').hidden=$('online_mode').checked;};
$('check-java').onclick=guarded(async()=>{const result=await api('/api/setup/java',{java:$('java').value});$('java-status').textContent=`Detected Java ${result.major}`;});
$('to-config').onclick=guarded(async()=>{
  if (!$('version').value) throw new Error('Choose a Minecraft version first.');
  if (!$('snapshot-label').hidden && !$('snapshot-confirm').checked) throw new Error('Confirm the snapshot warning.');
  const result=await api('/api/setup/java',{java:$('java').value});
  const required=versions.find(v=>v.id===$('version').value).java_major;
  if(result.major<required)throw new Error(`Java ${required}+ is required.`);
  show('configure',3);await persist();
});
$('back-version').onclick=()=>show('minecraft',2);
$('back-config').onclick=()=>show('configure',3);
$('to-review').onclick=guarded(async()=>{
  for(const id of fields) if(!$(id).checkValidity()) {$(id).reportValidity();return;}
  if(!$('eula').checked)throw new Error('Accept the Minecraft EULA to continue.');
  if(!$('online_mode').checked&&!$('offline-confirm').checked)throw new Error('Confirm the offline-mode warning.');
  $('summary').replaceChildren();
  for(const [key,value] of Object.entries(data()).filter(([k])=>fields.includes(k))) {
    const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key.replaceAll('_',' ');dd.textContent=String(value);$('summary').append(dt,dd);
  }
  $('review-description').textContent=status.settings.complete?'Minecraft must be stopped. A verified world backup will be created before replacing the jar.':'The official server will be downloaded and verified before installation.';
  show('review',4);await persist();
});
async function poll() {
  const result=await api('/api/setup/status');const job=result.job;
  $('job').hidden=false;$('progress').value=job.percent;$('job-message').textContent=job.message;
  if(job.state==='running') {poller=setTimeout(()=>poll().catch(e=>error(e.message)),1200);return;}
  for(const id of ['finish-start','finish-only','back-config'])$(id).disabled=false;
  if(job.state==='done') {show('done',4);$('done-message').textContent=job.message;}
  else if(job.state==='error') error(job.message);
}
async function finish(start) {
  await persist();await api('/api/setup/install',{...data(),start});
  for(const id of ['finish-start','finish-only','back-config'])$(id).disabled=true;
  await poll();
}
$('finish-start').onclick=guarded(()=>finish(true));
$('finish-only').onclick=guarded(()=>finish(false));
$('import').onclick=guarded(async()=>{await api('/api/setup/import',{});show('done',4);$('done-message').textContent='Imported existing server. Its world and running Minecraft process were left untouched.';});
$('logout').onclick=craftLogout;
document.addEventListener('change',()=>{if(auth?.authenticated&&currentStep>1)persist().catch(e=>error(e.message));});
(async()=>{
  auth=await api('/api/auth/status');
  if(!auth.authenticated) {
    show('access',1);$('token-label').hidden=auth.admin_exists;
    $('access-title').textContent=auth.admin_exists?'Welcome back':'Secure your dashboard';
    $('access-submit').textContent=auth.admin_exists?'Sign in':'Create administrator';
    $('password').autocomplete=auth.admin_exists?'current-password':'new-password';
    return;
  }
  $('logout').hidden=false;status=await api('/api/setup/status');
  const draft={...status.settings,...status.draft};
  for(const id of fields) if(draft[id]!==undefined&&id!=='version') {if($(id).type==='checkbox')$(id).checked=draft[id];else $(id).value=draft[id];}
  $('offline-label').hidden=$('online_mode').checked;
  if(status.existing_version&&!status.settings.complete){$('existing').hidden=false;$('existing-title').textContent=`Found Minecraft ${status.existing_version.id}${status.running?' · running':''}`;}
  show('minecraft',2);
  if(status.job.state==='running'){show('review',4);await poll();}
  $('snapshots').checked=Boolean(status.draft.snapshot_confirm);
  await loadVersions();
  if(status.draft.version===$('version').value)$('snapshot-confirm').checked=Boolean(status.draft.snapshot_confirm);
  // Resume the last configuration step once the catalog is available.
  if(status.draft.step===3&&$('version').value)show('configure',3);
  if(status.draft.step===4&&$('version').value)$('to-review').click();
})().catch(e=>error(e.message));
