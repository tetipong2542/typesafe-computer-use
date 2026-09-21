export async function fetchWithDeadline(url,options,{fetchCall=fetch,timeoutMs=60000,provider='Jev',onTimeout}={}){
 const controller=new AbortController(),outer=options.signal;
 const timer=setTimeout(()=>controller.abort(new Error('request deadline')),timeoutMs);
 try{
  const signal=outer?AbortSignal.any([outer,controller.signal]):controller.signal;
  const response=await fetchCall(url,{...options,signal});
  const text=await response.text();
  return new Response(text,{status:response.status,statusText:response.statusText,headers:response.headers});
 }catch(error){
  if(outer?.aborted)throw outer.reason??error;
  if(controller.signal.aborted){onTimeout?.();throw new Error(`${provider} provider request timeout: ${timeoutMs}ms`);}
  throw error;
 }finally{clearTimeout(timer);}
}
