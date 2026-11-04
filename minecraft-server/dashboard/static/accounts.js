/* Administrator-only player invitations. Secrets are displayed once, never persisted in the browser. */
(() => {
  const $=id=>document.getElementById(id);
  async function api(data,method='GET'){
    const response=await fetch('/api/accounts',method==='GET'?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(!response.ok){const text=await response.text();throw new Error(new DOMParser().parseFromString(text,'text/html').title||'Account request failed');}
    return response.json();
  }
  function invite(result){
    $('player-invite-result').hidden=false;
    $('player-invite-title').textContent=`Invitation for ${result.username} · expires in 48 hours`;
    $('player-invite-link').value=`${location.origin}/join#invite=${encodeURIComponent(result.token)}`;
    $('player-invite-link').focus();$('player-invite-link').select();
  }
  async function load(){
    try{
      const data=await api(),select=$('account-player');const selected=select.value;
      select.replaceChildren();
      const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent=data.players.length?'Select a saved Minecraft player':'No saved players yet — join Minecraft first';select.append(placeholder);
      const assigned=new Set(data.accounts.map(a=>a.player_uuid));
      for(const player of data.players){const option=document.createElement('option');option.value=player.uuid;option.textContent=`${player.name} · ${player.uuid.slice(0,8)}${assigned.has(player.uuid)?' · linked':''}`;option.disabled=assigned.has(player.uuid);select.append(option);}
      if([...select.options].some(o=>o.value===selected&&!o.disabled))select.value=selected;
      const list=$('dashboard-accounts');list.replaceChildren();
      for(const account of data.accounts){
        const row=document.createElement('div');row.className='dashboard-account-row';
        const info=document.createElement('div'),name=document.createElement('strong'),detail=document.createElement('small');
        name.textContent=account.username;detail.textContent=`${account.minecraft_name} · ${account.status==='active'?'Active player login':account.invite_expires*1000<Date.now()?'Invitation expired':'Invitation pending'}`;
        info.append(name,detail);row.append(info);
        for(const [label,action] of [['Reset invitation','reset'],['Revoke access','delete']]){
          const button=document.createElement('button');button.className='map-player-action';button.textContent=label;
          button.onclick=async()=>{
            if(!confirm(`${label} for ${account.username}? Existing dashboard sessions will end. Minecraft data and permissions are unaffected.`))return;
            button.disabled=true;
            try{const result=await api({id:account.id,action},action==='delete'?'DELETE':'POST');if(action==='reset')invite(result);else $('player-invite-result').hidden=true;await load();}
            catch(e){showToast(e.message,'error');}finally{button.disabled=false;}
          };row.append(button);
        }list.append(row);
      }
      if(!data.accounts.length){const p=document.createElement('p');p.className='card-subtitle';p.textContent='No player logins yet. Invite a saved player to give them read-only dashboard access.';list.append(p);}
    }catch(e){$('account-error').textContent=e.message;}
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if(!$('account-create'))return;
    $('account-create').onsubmit=async event=>{
      event.preventDefault();$('account-error').textContent='';$('account-invite-button').disabled=true;
      try{const result=await api({username:$('account-username').value.trim(),player_uuid:$('account-player').value},'POST');invite(result);$('account-username').value='';await load();}
      catch(e){$('account-error').textContent=e.message;}finally{$('account-invite-button').disabled=false;}
    };
    $('invite-copy').onclick=async()=>{try{await navigator.clipboard.writeText($('player-invite-link').value);showToast('Invitation copied','success');}catch{$('player-invite-link').focus();$('player-invite-link').select();showToast('Link selected — copy it to share','info');}};
    $('invite-close').onclick=()=>{$('player-invite-link').value='';$('player-invite-result').hidden=true;};
    $('accounts-refresh').onclick=load;load();
  });
})();
