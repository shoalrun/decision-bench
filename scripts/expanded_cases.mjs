#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const sha=x=>createHash('sha256').update(x).digest('hex');
const canon=x=>JSON.stringify(x,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const clone=x=>JSON.parse(JSON.stringify(x));
const eq=(a,b)=>canon(a)===canon(b);
const token=(f,v)=>sha(f+':'+v).slice(0,6);
const locales=()=>Intl.DateTimeFormat().resolvedOptions().locale;
const specs={synonyms:{file:'framework/src/search-synonyms.ts',functions:['validateSearchSynonyms','phraseMatches','queryUnits']},readers:{file:'framework/src/reader-filters.ts',functions:['validateReaderSelection','pageReaderFilters']}};
async function load(text){
  const js=stripTypeScriptTypes(text,{mode:'strip'});
  if(/^\s*import\s/m.test(js))throw new Error('Unexpected runtime import');
  return import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
}
function execute(apis,input){
  const spec=specs[input.module];
  if(!spec?.functions.includes(input.function))throw new Error('Unsupported oracle function');
  try{const result=apis[input.module][input.function](...input.args);return{ok:true,result:result===undefined?{$undefined:true}:clone(result)}}
  catch(error){return{ok:false,error:{name:error.name,message:error.message}}}
}
function derive(c,outcome){
  if(c.task==='function-validity')return outcome.ok?'yes':'no';
  if(c.task==='boolean-result'){
    if(!outcome.ok||typeof outcome.result!=='boolean')throw new Error('Expected boolean result');
    return outcome.result?'yes':'no';
  }
  const hits=Object.entries(c.options).filter(([,v])=>eq(v,outcome));
  if(hits.length!==1)throw new Error('Need one exact outcome option');
  return hits[0][0];
}
function optionMap(values,salt){
  return Object.fromEntries(values.map((value,i)=>({value,rank:sha(salt+':'+i)})).sort((a,b)=>a.rank.localeCompare(b.rank)).map((x,i)=>['option_'+String.fromCharCode(97+i),x.value]));
}
function config(seed){
  const t=token('config',seed),a='author-'+t,r='reader-'+t,p='api-'+t,w='web-'+t,g='guide-'+t,z='reference-'+t;
  return{facets:{audience:{label:'Audience',options:{[a]:'Author',[r]:'Reader'}},product:{label:'Product',options:{[p]:'API',[w]:'Web'}},area:{label:'Area',options:{[g]:'Guide',[z]:'Reference'}},subarea:{label:'Subarea',options:{['start-'+t]:'Start',['advanced-'+t]:'Advanced'}}},typeDefaults:{guide:{audience:[a],product:[p]}},controls:{sidebar:true}};
}
function keys(c){return Object.fromEntries(Object.entries(c.facets).map(([k,v])=>[k,Object.keys(v.options)]))}
function generate(){
 const byFn={};
 function add(fn,module,family,v,args,task){
   const c={id:`${fn}-${family}-${v+1}`,task,group:fn+'/'+family,split:family.startsWith('dev-')?'dev':'test',query:task==='function-validity'?`Does ${fn} return normally without throwing for this input?`:task==='boolean-result'?`Does ${fn} return true for these arguments?`:`Which exact result or exception does ${fn} produce for these arguments?`,input:{module,function:fn,args}};
   (byFn[fn]??=[]).push(c);
 }
 const synonymFamilies=['dev-spacing','dev-root-shape','dev-duplicates','dev-phrases','dev-term-types','test-punctuation','test-unicode','test-group-shape','test-empty-multiple','test-whitespace-collisions'];
 for(let f=0;f<10;f++)for(let v=0;v<8;v++){
   const t=token('syn-'+f,v),a='term'+t,b='other'+t;
   let args;
   if(f===0)args=[[[v%2?a.toUpperCase():' '+a+' ',b+'\t']]];
   if(f===1){const root=v<4?[null,false,{},'terms'][v]:[['root'+t,'alias'+t]];args=[root]}
   if(f===2)args=[v%2?[[a,b],[' '+a.toUpperCase()+' ','third'+t]]:[[a,a.toUpperCase()]]];
   if(f===3)args=[[[a+' step',b+' route'],['next'+t,'after'+t]]];
   if(f===4)args=[[[a,[42,null,true,{},[], '', '  ', {term:'x'}][v]]]];
   if(f===5)args=[[[a,['a-b','a_b','a.b','a/b','c++','one:two','"term"','@term'][v]]]];
   if(f===6)args=[[[['café','文档','δοκιμή','текст','日本語','mañana','naïve','résumé'][v]+t,b]]];
   if(f===7){const group=v<4?[[],['single'+t],null,{}][v]:['group'+t,'alias'+t,...(v===7?['third'+t]:[])];args=[[group]]}
   if(f===8)args=[v<4?[[a,b],['more'+t,'extra'+t]]:[[a,b],[b,'last'+t]]];
   if(f===9)args=[v<4?[[a+'\t step',b+'\nroute']]:[[a+' step',a+'\tstep']]];
   add('validateSearchSynonyms','synonyms',synonymFamilies[f],v,args,'function-validity');
 }
 const phraseFamilies=['dev-word','dev-plural','dev-start-boundary','dev-end-boundary','dev-phrase-space','test-unicode-boundary','test-regex-literals','test-final-plural','test-case','test-number-boundary'];
 for(let f=0;f<10;f++)for(let v=0;v<8;v++){
   const t=token('phrase-'+f,v);let phrase='topic'+t,value;
   if(f===0)value=v%2?'x'+phrase:'use '+phrase+' now';
   if(f===1)value=v%2?phrase+'ing':phrase+'s';
   if(f===2)value=v%2?'a'+phrase:'.'+phrase;
   if(f===3)value=v%2?phrase+'a':phrase+'!';
   if(f===4){phrase='read '+phrase;value=v%2?phrase.replace(' ','-'):phrase.replace(' ','\t')}
   if(f===5)value=v%2?'文'+phrase:'文 '+phrase+' 文';
   if(f===6){phrase=['c++','a.b','x(y)','a[b]','x?y','a$b','x|y','a*b'][v]+t;value=v%2?phrase.replace(/[^a-z0-9]/g,'q'):phrase}
   if(f===7){phrase='read '+phrase;value=v%2?'reads '+phrase.slice(5):phrase+'s'}
   if(f===8)value=v%2?'X'+phrase.toUpperCase():phrase.toUpperCase();
   if(f===9){phrase=String(4200+v);value=v%2?'9'+phrase:'.'+phrase+'.'}
   add('phraseMatches','synonyms',phraseFamilies[f],v,[value,phrase],'boolean-result');
 }
 const queryFamilies=['dev-single','dev-longest-phrase','dev-edge-punctuation','dev-plural','dev-repeated','test-case-folding','test-overlap','test-unknown','test-whitespace','test-alternative-order'];
 for(let f=0;f<10;f++)for(let v=0;v<8;v++){
   const t=token('query-'+f,v),a='topic'+t,b='other'+t,c='extra'+t;
   let query=a,groups=[[a,b]];
   if(f===1){query='read '+a+' now';groups=[['read '+a,b],['read',c]]}
   if(f===2)query=['(', '[', '"', '.', '!', '?', ':', ';'][v]+a+',';
   if(f===3)query=a+'s';
   if(f===4)query=a+' '+a+' now';
   if(f===5)query=a.toUpperCase();
   if(f===6){query='one '+a+' one';groups=[['one '+a,b],['one',c]]}
   if(f===7)query='unknown'+t+' '+a;
   if(f===8){query='read\t '+a+'\nnow';groups=[['read '+a,b]]}
   if(f===9){query=b;groups=[[a,b,c]]}
   add('queryUnits','synonyms',queryFamilies[f],v,[query,groups],'function-outcome');
 }
 const selectionFamilies=['dev-combinations','dev-list-types','dev-facet-membership','dev-duplicate-choices','dev-choice-membership','test-root-shape','test-prototype-lookups','test-multiselect','test-empty-lists','test-mixed-facets'];
 for(let f=0;f<10;f++)for(let v=0;v<8;v++){
   const cfg=config('selection-'+f+'-'+v),k=keys(cfg);let value;
   if(f===0)value={audience:v&1?[k.audience[0]]:[],product:v&2?[k.product[1]]:[],area:v&4?[k.area[0]]:[]};
   if(f===1)value={audience:['author',null,42,true,{},['unknown'],[null],[1]][v]};
   if(f===2)value=v<4?{product:[k.product[v%2]]}:{['missing'+token('missing',v)]:[]};
   if(f===3)value={audience:v<4?[...k.audience]:[k.audience[0],k.audience[0]]};
   if(f===4)value={area:v<4?[k.area[v%2]]:['unconfigured'+v]};
   if(f===5)value=v<4?{subarea:[k.subarea[v%2]]}:[null,[],true,'reader'][v-4];
   if(f===6)value={audience:[['constructor','toString','valueOf','hasOwnProperty','missing','auditor','AUTHOR','reader '][v]]};
   if(f===7)value={audience:v<4?[...k.audience]:[...k.audience,k.audience[0]],product:[k.product[v%2]]};
   if(f===8)value=v<4?{audience:[],area:[]}:{audience:[],area:[['unknown',null,0,false][v-4]]};
   if(f===9)value=v<4?{product:[k.product[0]],area:[k.area[1]]}:{product:[k.product[0]],area:'not-a-list'};
   add('validateReaderSelection','readers',selectionFamilies[f],v,[value,cfg,'selection'],'function-validity');
 }
 const pageFamilies=['dev-type-defaults','dev-explicit-overrides','dev-audiences','dev-explicit-facets','dev-no-config','test-category-shorthand','test-shorthand-conflict','test-empty-overrides','test-invalid-selection','test-missing-facet'];
 for(let f=0;f<10;f++)for(let v=0;v<8;v++){
   let cfg=config('page-'+f+'-'+v);const k=keys(cfg);let fm={type:'guide'};
   if(f===0)fm={type:v%2?'reference':'guide'};
   if(f===1)fm.readerFilters={audience:v%2?[]:[k.audience[1]],product:[k.product[v%2]]};
   if(f===2)fm.audiences=v%2?[...k.audience]:[k.audience[1]];
   if(f===3)fm.readerFilters={area:[k.area[v%2]],subarea:[k.subarea[(v>>1)%2]]};
   if(f===4){cfg=null;if(v>=4)fm.readerFilters=[{},null,[],false][v-4];else fm.audiences=['ignored'+v]}
   if(f===5){const facet=['product','area','subarea'][v%3];fm[facet]=v%2?[k[facet][0],k[facet][1]]:k[facet][0]}
   if(f===6){const facet=['audience','product','area','subarea'][v%4];if(facet==='audience')fm.audiences=[k.audience[0]];else fm[facet]=k[facet][0];fm.readerFilters={[facet]:[k[facet][1]]}}
   if(f===7)fm.readerFilters={audience:[],...(v%2?{product:[]}:{area:[]})};
   if(f===8)fm.readerFilters=v%2?{missing:[]}:{audience:[v%4?null:'unknown']};
   if(f===9){delete cfg.facets.audience;delete cfg.typeDefaults.guide.audience;fm.audiences=[k.audience[0]];fm.type=v%2?'guide':'reference'}
   add('pageReaderFilters','readers',pageFamilies[f],v,[fm,cfg,'example.mdx'],'function-outcome');
 }
 return byFn;
}
function wrongOptions(c,actual,apis){
 if(c.input.function==='pageReaderFilters'){
   const [fm,cfg,file]=c.input.args;
   const base=config('distractor-'+c.id),k=keys(base);
   const errors=[];
   const addError=x=>{if(!x.ok&&!errors.some(y=>eq(x,y)))errors.push(x)};
   if(!actual.ok)addError(actual);
   const bad=[
     [{type:'guide',audiences:[k.audience[0]],readerFilters:{audience:[k.audience[1]]}},base,file],
     ...['product','area','subarea'].map(facet=>[{type:'guide',[facet]:k[facet][0],readerFilters:{[facet]:[k[facet][1]]}},base,file]),
     [{type:'guide',readerFilters:{unconfigured:[]}},base,file],
     [{type:'guide',readerFilters:{audience:[null]}},base,file],
     [{type:'guide',readerFilters:{}},null,file]
   ];
   for(const args of bad)addError(execute(apis,{module:'readers',function:'pageReaderFilters',args}));
   const normal=[];
   const addNormal=result=>{const x={ok:true,result};if(!normal.some(y=>eq(x,y)))normal.push(x)};
   if(actual.ok)addNormal(actual.result);
   addNormal(cfg?.typeDefaults?.[fm.type]??{});
   if(fm.readerFilters&&typeof fm.readerFilters==='object'&&!Array.isArray(fm.readerFilters))addNormal(fm.readerFilters);
   addNormal({});
   addNormal({audience:[]});
   for(const facet of ['audience','product','area','subarea']){
     const available=cfg?.facets?.[facet]?Object.keys(cfg.facets[facet].options):[];
     for(const choice of available)addNormal({...clone(actual.ok?actual.result:{}),[facet]:[choice]});
   }
   if(normal.length<2||errors.length<2)throw new Error('Need two return and two exception choices');
   return optionMap([...normal.slice(0,2),...errors.slice(0,2)],c.id);
 }

 const pool=[];const push=x=>{if(!eq(x,actual)&&!pool.some(y=>eq(y,x)))pool.push(x)};
 if(actual.ok){
   const value=actual.result;
   if(Array.isArray(value)){
     if(value.length>1)push({ok:true,result:value.slice(0,1)});
     const first=clone(value);if(first.length){first[0].alternatives=[];push({ok:true,result:first})}
     const plural=clone(value);if(plural.length){plural[0].term+='s';push({ok:true,result:plural})}
     const changed=clone(value);if(changed.length){changed[0].alternatives=[changed[0].term,...changed[0].alternatives];push({ok:true,result:changed})}
     const reversed=clone(value);if(reversed.length){reversed[0].alternatives.reverse();push({ok:true,result:reversed})}
     push({ok:true,result:[]});
   }else{
     const entries=Object.entries(value);if(entries.length)push({ok:true,result:Object.fromEntries(entries.slice(1))});
     for(const facet of ['audience','product','area','subarea']){
       const cfg=c.input.args[1];const choices=cfg?.facets?.[facet]?Object.keys(cfg.facets[facet].options):['author','reader'];
       for(const choice of choices)push({ok:true,result:{...clone(value),[facet]:[choice]}});
       push({ok:true,result:{...clone(value),[facet]:[]}});
     }
   }
 }else{
   throw new Error('Unexpected exception in query outcome construction');
 }
 if(pool.length<3)throw new Error('Insufficient plausible distinct choices: '+c.id);
 return optionMap([actual,...pool.slice(0,3)],c.id);
}
async function capture(source,out){
 const commit=execFileSync('git',['-C',source,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
 const references={},apis={};
 for(const [id,spec] of Object.entries(specs)){
   if(execFileSync('git',['-C',source,'status','--porcelain','--',spec.file],{encoding:'utf8'}).trim())throw new Error('Dirty source module');
   const text=await readFile(path.join(source,spec.file),'utf8');
   references[id]={text,sha256:sha(text),source:{repository:'https://github.com/shoalrun/shoalrun',commit,path:spec.file,license:'Apache-2.0'}};
   apis[id]=await load(text);
 }
 const groups=generate();
 const outcomes=Object.fromEntries(Object.entries(groups).map(([fn,cases])=>[fn,cases.map(c=>execute(apis,c.input))]));
 const cases=[];
 for(let i=0;i<80;i++)for(const fn of Object.keys(groups)){
   const c=groups[fn][i],outcome=outcomes[fn][i];
   if(c.task==='function-outcome')c.options=wrongOptions(c,outcome,apis);
   c.expected=derive(c,outcome);c.oracle={call:clone(c.input),outcome};cases.push(c);
 }
 const data={schema_version:3,name:path.basename(out).replace(/\.json$/,''),label_origin:'Expected labels derived by executing frozen source, with model-visible call binding and deterministic replay.',scope:'Exact outcomes of five functions on the supplied inputs. The model receives one source module as its rules. This is not full-project validation or a general model leaderboard.',split_policy:'Five development and five test mutation families per function, with eight cases per family. Related variants stay together. Shared source rules; no unseen-source or statistically independent-case claim.',references,oracle_runtime:{node:process.version,locale:locales(),serialization:'JSON; undefined return is {$undefined:true}; undefined object properties omitted; errors retain name and message.'},cases};
 const calls=new Map();for(const c of cases){const key=canon(c.input);if(calls.has(key))throw new Error('Duplicate call: '+c.id+' and '+calls.get(key));calls.set(key,c.id)}
 await writeFile(out,JSON.stringify(data,null,2)+'\n',{flag:'wx'});
 console.log('Captured '+cases.length+' executable cases -> '+out);
 for(const fn of Object.keys(groups)){
   const cs=cases.filter(c=>c.input.function===fn);
   console.log(fn,cs.length,'cases',cs.filter(c=>c.split==='dev').length,'dev',cs.filter(c=>c.split==='test').length,'test');
 }
}
async function verify(file){
 const data=JSON.parse(await readFile(file,'utf8'));const apis={};
 if(data.schema_version!==3)throw new Error('Expected schema 3');
 if(data.oracle_runtime.node!==process.version||data.oracle_runtime.locale!==locales())throw new Error('Oracle runtime/locale mismatch');
 for(const [id,reference] of Object.entries(data.references)){
   if(sha(reference.text)!==reference.sha256)throw new Error('Source hash mismatch');apis[id]=await load(reference.text);
 }
 const seen=new Set();for(const c of data.cases){
   if(!eq(c.input,c.oracle.call))throw new Error('Visible input/oracle mismatch: '+c.id);
   const key=canon(c.input);if(seen.has(key))throw new Error('Duplicate input call');seen.add(key);
   const actual=execute(apis,c.input);
   if(!eq(actual,c.oracle.outcome))throw new Error('Oracle outcome mismatch: '+c.id);
   if(derive(c,actual)!==c.expected)throw new Error('Expected label mismatch: '+c.id);
 }
 console.log('Verified '+data.cases.length+' expanded known answers on '+process.version+'.');
}
const [mode,arg,out]=process.argv.slice(2);
if(mode==='capture'&&arg&&out)await capture(path.resolve(arg),out);
else if(mode==='verify'&&arg)await verify(arg);
else throw new Error('Usage: node scripts/expanded_cases.mjs capture SOURCE NEW_DATASET | verify DATASET');
