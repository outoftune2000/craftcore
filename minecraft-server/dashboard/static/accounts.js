/* Administrator-only player invitations. Secrets are displayed once, never persisted in the browser. */
(() => {
  const $=id=>document.getElementById(id);
  let lastData='';
  async function api(data,method='GET'){
    const response=await fetch('/api/accounts',method==='GET'?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(!response.ok){const text=await response.text();let message;try{message=JSON.parse(text).error;}catch{}throw new Error(message||new DOMParser().parseFromString(text,'text/html').title||'Account request failed');}
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
      const data=await api(),signature=JSON.stringify(data);$('account-error').textContent='';if(signature===lastData)return;lastData=signature;
      const select=$('account-player');const selected=select.value;
      renderRequests(data);
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
  function renderRequests(data){
    const requests=data.registrations||[],notice=$('account-request-notice');
    notice.hidden=!requests.length;notice.textContent=`Account requests · ${requests.length}`;
    $('registration-count').textContent=`${requests.length} pending`;
    const list=$('registration-list'),selections=new Map([...list.querySelectorAll('select[data-request]')].map(select=>[select.dataset.request,select.value]));
    list.replaceChildren();
    const assigned=new Set(data.accounts.map(account=>account.player_uuid));
    for(const request of requests){
      const row=document.createElement('article');row.className='registration-request';
      const heading=document.createElement('strong');heading.textContent=request.username;
      const details=document.createElement('p');details.textContent=`Claims Minecraft name: ${request.minecraft_name} · Requested ${new Date(request.created_at*1000).toLocaleString()}`;
      const label=document.createElement('label');label.textContent='Verify and link Minecraft player';
      const select=document.createElement('select');select.dataset.request=request.id;select.setAttribute('aria-label',`Minecraft player for ${request.username}`);
      const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Select the verified saved player';select.append(placeholder);
      for(const player of data.players){const option=document.createElement('option');option.value=player.uuid;option.textContent=`${player.name} · ${player.uuid.slice(0,8)}${assigned.has(player.uuid)?' · already linked':''}`;option.disabled=assigned.has(player.uuid);select.append(option);}
      const preferred=selections.get(request.id)||data.players.find(player=>player.name.toLowerCase()===request.minecraft_name.toLowerCase()&&!assigned.has(player.uuid))?.uuid;
      if(preferred&&[...select.options].some(option=>option.value===preferred&&!option.disabled))select.value=preferred;
      label.append(select);row.append(heading,details,label);
      const buttons=document.createElement('div');buttons.className='invite-actions';
      for(const [text,action] of [['Approve account','approve'],['Reject request','reject']]){
        const button=document.createElement('button');button.className='map-player-action';button.textContent=text;
        button.onclick=async()=>{
          if(action==='approve'&&!select.value){showToast('Select the verified Minecraft player first','error');select.focus();return;}
          const message=action==='approve'?`Approve ${request.username} for ${select.selectedOptions[0].textContent}? This enables their read-only player login.`:`Reject the account request from ${request.username}? No account will be created.`;
          if(!confirm(message))return;
          button.disabled=true;
          try{await api({action,id:request.id,player_uuid:select.value},'POST');showToast(action==='approve'?'Player account approved':'Request rejected','success');await load();}
          catch(e){showToast(e.message,'error');}finally{button.disabled=false;}
        };buttons.append(button);
      }
      row.append(buttons);list.append(row);
    }
    if(!requests.length){const empty=document.createElement('p');empty.className='card-subtitle';empty.textContent='No account requests awaiting approval.';list.append(empty);}
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
    $('account-request-notice').onclick=()=>{document.querySelector('[data-tab="tab-players"]').click();$('registration-requests').scrollIntoView({block:'start'});};
    $('accounts-refresh').onclick=load;load();
    setInterval(()=>{if(!document.hidden)load();},10000);
  });
})();
