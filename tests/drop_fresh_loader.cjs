const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const html=fs.readFileSync(process.argv[2],'utf8'), asset=fs.readFileSync(process.argv[3],'utf8');
const start=html.indexOf('  var freshUI ='), end=html.indexOf('  function refreshDropPicks(',start);
const source=html.slice(start,end);
const hash=require('node:crypto').createHash('sha256').update(asset).digest('hex').slice(0,12);
assert(source.includes('/drop-fresh.js?v='+hash));
(async()=>{
  for(const fail of [false,true]) for(const saved of [false,true])
    for(const view of ['trending','search','analyze','category','sort','saved']) {
    const frames=[],tasks=[],urls=[];let renders=0,restores=0,mounted=0,tab=true;
    const ctx={state:{sortKey:'top'},savedOn:()=>true,savedDeals:{},frontPageView:()=>tab,
      dealKey:d=>d.key,isLocked:d=>d.locked,dropPickIndex:()=>123,reduceMotion:()=>true,cardEls:()=>[],
      render(){renders++;},restoreInitialBrowse(){restores++;},rememberBrowse(){},renderTabs(){},finishBrowseAction(){},
      loadedOnce:true,requestAnimationFrame(fn){frames.push(fn);},setTimeout(fn){tasks.push(fn);},
      testImport(url){urls.push(url);return fail?Promise.reject(Error('offline')):Promise.resolve({default(host){
        mounted++;assert.equal(host.state,ctx.state);ctx.state={...ctx.state};assert.equal(host.state,ctx.state);return {};
      }});}};
    if(!saved) { delete ctx.savedOn;delete ctx.savedDeals; }
    vm.createContext(ctx);
    vm.runInContext(source.replace('import(', 'testImport('),ctx);
    assert.equal(urls.length,0);assert.equal(frames.length,1);
    assert(ctx.freshView());tab=false;assert(!ctx.freshView());tab=true;
    frames.shift()();assert.equal(urls.length,0);assert.equal(tasks.length,1,'network after paint opportunity');
    // A visitor changes view while the lazy import is outstanding.
    if(['search','analyze','category'].includes(view)) tab=false;
    if(view==='sort') ctx.state.sortKey='price';
    if(view==='saved') { if(saved) ctx.savedDeals.view=true; else tab=false; }
    tasks.shift()();await new Promise(resolve=>setImmediate(resolve));
    assert.deepEqual(urls,['/drop-fresh.js?v='+hash]);assert.equal(renders,Number(view==='trending'),view);
    assert.equal(mounted,fail?0:1);assert.equal(restores,Number(!fail && view==='trending'));assert.equal(ctx.freshFailed,fail);
    if(fail) assert(!ctx.freshView(),'failed asset releases the static-wall render guard');
  }
  console.log('Lazy drop loader passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
