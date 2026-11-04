(() => {
  const $=id=>document.getElementById(id);
  const error=message=>{$('register-error').textContent=message;$('register-error').hidden=!message;};
  const ready=fetch('/api/auth/status').then(r=>r.json()).then(data=>{
    if(!data.registration_open){error('Player registration opens after the administrator completes server setup.');$('register-submit').disabled=true;}
    return data;
  }).catch(()=>{error('Could not reach the server. Refresh to try again.');return null;});
  $('registration-form').onsubmit=async event=>{
    event.preventDefault();error('');
    if($('register-password').value!==$('register-confirm').value){error('Passwords do not match');return;}
    $('register-submit').disabled=true;
    try{
      const state=await ready;if(!state?.registration_open)throw new Error('Player registration is not available yet.');
      const response=await fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('register-username').value.trim(),minecraft_name:$('register-minecraft').value.trim(),password:$('register-password').value})});
      if(!response.ok){const text=await response.text();let message;try{message=JSON.parse(text).error;}catch{}throw new Error(message||new DOMParser().parseFromString(text,'text/html').title||'Could not submit your account request');}
      $('register-password').value='';$('register-confirm').value='';$('register-section').hidden=true;$('registration-success').hidden=false;
    }catch(e){error(e.message);$('register-submit').disabled=false;}
  };
})();
