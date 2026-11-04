(() => {
  const $=id=>document.getElementById(id);
  const token=new URLSearchParams(location.hash.slice(1)).get('invite');
  if(token){$('invite-token').value=token;history.replaceState(null,'',location.pathname);}
  // Issue the anti-CSRF cookie before activation can be submitted.
  const ready=fetch('/api/auth/status');
  $('join-form').onsubmit=async event=>{
    event.preventDefault();$('error').hidden=true;
    if($('join-password').value!==$('join-confirm').value){$('error').textContent='Passwords do not match';$('error').hidden=false;return;}
    $('join-submit').disabled=true;
    try{
      await ready;
      const response=await fetch('/api/auth/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('invite-token').value,password:$('join-password').value})});
      if(!response.ok){const text=await response.text();throw new Error(new DOMParser().parseFromString(text,'text/html').title||'Activation failed');}
      $('join-password').value='';$('join-confirm').value='';$('invite-token').value='';location.assign('/');
    }catch(e){$('error').textContent=e.message;$('error').hidden=false;}finally{$('join-submit').disabled=false;}
  };
})();
