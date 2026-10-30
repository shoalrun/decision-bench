#!/usr/bin/env node
import { readFile,writeFile } from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {stripTypeScriptTypes} from 'node:module';
const sha=x=>createHash('sha256').update(x).digest('hex');
const canon=x=>JSON.stringify(x,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
const eq=(a,b)=>canon(a)===canon(b);
const clone=x=>JSON.parse(JSON.stringify(x));
const dependencies={validateSearchSynonyms:['validateSearchSynonyms'],phraseMatches:['phraseMatches'],queryUnits:['queryUnits'],validateReaderSelection:['object','validateReaderSelection'],pageReaderFilters:['object','validateReaderSelection','pageReaderFilters']};
const moduleFor=fn=>['validateSearchSynonyms','phraseMatches','queryUnits'].includes(fn)?'synonyms':'readers';
async function load(text){
 const js=stripTypeScriptTypes(text,{mode:'strip'});
 if(/^\s*import\s/m.test(js))throw new Error('Runtime imports are not supported');
 return import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
}
function execute(api,call){
 if(!Object.hasOwn(dependencies,call.function))throw new Error('Unsupported function');
 try{const result=api[call.function](...call.args);return{ok:true,result:result===undefined?{$undefined:true}:clone(result)}}
 catch(error){return{ok:false,error:{name:error.name,message:error.message}}}
}
function expected(c,outcome){
 if(c.task==='function-validity')return outcome.ok?'yes':'no';
 if(c.task==='boolean-result'){
   if(!outcome.ok||typeof outcome.result!=='boolean')throw new Error('Expected boolean');
   return outcome.result?'yes':'no';
 }
 const matches=Object.entries(c.options).filter(([,x])=>eq(x,outcome));
 if(matches.length!==1)throw new Error('Expected one matching choice');
 return matches[0][0];
}
async function verifyData(data){
 if(data.schema_version!==4)throw new Error('Expected schema 4');
 const base=data.base_fixture;
 if(sha(canon(base))!==data.base_fixture_hash)throw new Error('Base fixture hash mismatch');
 if(base.oracle_runtime.node!==process.version||base.oracle_runtime.locale!==Intl.DateTimeFormat().resolvedOptions().locale)throw new Error('Runtime/locale mismatch');
 const full={},focused={};
 for(const [id,ref] of Object.entries(base.references)){
   if(sha(ref.text)!==ref.sha256)throw new Error('Full source hash mismatch');full[id]=await load(ref.text);
 }
 for(const [fn,ref] of Object.entries(data.focused_sources)){
   if(sha(ref.text)!==ref.sha256||ref.original_source_sha256!==base.references[moduleFor(fn)].sha256)throw new Error('Focused source hash mismatch');
   focused[fn]=await load(ref.text);
 }
 for(const [fn,ref] of Object.entries(data.rules)){
   if(!dependencies[fn]||sha(ref.text)!==ref.sha256||!ref.text.trim())throw new Error('Rule hash mismatch');
 }
 const byId=new Map(base.cases.map(c=>[c.id,c]));
 for(const c of base.cases){
   if(!eq(c.input,c.oracle.call))throw new Error('Base visible-call mismatch');
   const outcome=execute(full[c.input.module],c.input);
   if(!eq(outcome,c.oracle.outcome)||expected(c,outcome)!==c.expected)throw new Error('Base oracle mismatch');
   if(!eq(execute(focused[c.input.function],c.input),outcome))throw new Error('Focused/full behavior differs: '+c.id);
 }
 const seen=new Set();
 for(const c of data.cases){
   const baseCase=byId.get(c.base_case_id);
   if(!baseCase||!['full','focused','rules'].includes(c.presentation))throw new Error('Invalid paired case');
   const key=c.base_case_id+'/'+c.presentation;
   if(seen.has(key))throw new Error('Duplicate paired presentation');seen.add(key);
   const copy=clone(c);delete copy.base_case_id;delete copy.presentation;copy.id=baseCase.id;
   if(!eq(copy,baseCase))throw new Error('Paired case changed input, choices, expected answer, or split');
 }
 if(seen.size!==base.cases.length*3)throw new Error('Incomplete paired view coverage');
 return base.cases.length;
}
async function capture(baseFile,rulesFile,out){
 const base=JSON.parse(await readFile(baseFile,'utf8'));
 const rulesInput=JSON.parse(await readFile(rulesFile,'utf8'));
 const {parse,version}=await import('acorn'); // Capture only; replay needs no parser package.
 const focused_sources={},rules={};
 for(const [fn,names] of Object.entries(dependencies)){
   const ref=base.references[moduleFor(fn)];const js=stripTypeScriptTypes(ref.text,{mode:'strip'});
   const ast=parse(js,{ecmaVersion:'latest',sourceType:'module'});const declarations=new Map();
   for(const raw of ast.body){
     const node=raw.type==='ExportNamedDeclaration'?raw.declaration:raw;
     if(node?.type==='FunctionDeclaration')declarations.set(node.id.name,js.slice(node.start,node.end));
     if(node?.type==='VariableDeclaration')for(const decl of node.declarations)if(decl.id.type==='Identifier')declarations.set(decl.id.name,js.slice(node.start,node.end));
   }
   const pieces=names.map(name=>{if(!declarations.has(name))throw new Error('Missing dependency '+name);return declarations.get(name)});
   const text=pieces.join('\n\n')+'\n\nexport { '+fn+' };\n';
   focused_sources[fn]={text,sha256:sha(text),original_source_sha256:ref.sha256,dependencies:names};
   const ruleText=rulesInput.rules[fn];if(!ruleText)throw new Error('Missing rules');
   rules[fn]={text:ruleText,sha256:sha(ruleText),basis:'Authored textual contract reviewed against the frozen source; not an executable replacement for the oracle.'};
 }
 const cases=[];
 for(const c of base.cases)for(const presentation of ['full','focused','rules'])cases.push({...clone(c),id:c.id+'--'+presentation,base_case_id:c.id,presentation});
 const data={schema_version:4,name:'shoalrun-presentations-v1',base_fixture:base,base_fixture_hash:sha(canon(base)),focused_sources,rules,cases,
   label_origin:'Same source-executed expected answers as the 400-case base fixture. No answer changes between presentations.',
   scope:'Paired presentation comparison: full modules, executable-equivalent focused code, and reviewed textual rules. This is not a fresh blind generalization test.',
   design:'Every base question, input, choice, split and expected answer is unchanged across three views. Jobs are shuffled together by the deterministic runner. Textual rules can make runtime assumptions more explicit and need not match focused code token length; do not attribute effects solely to language.',
   capture_tools:{node:process.version,acorn:version}};
 await verifyData(data);
 await writeFile(out,JSON.stringify(data,null,2)+'\n',{flag:'wx'});
 console.log('Captured '+cases.length+' presentation cases (400 matched triplets).');
 for(const fn of Object.keys(dependencies))console.log(fn,'focused chars',focused_sources[fn].text.length,'rule chars',rules[fn].text.length);
}
const [mode,arg,second,out]=process.argv.slice(2);
if(mode==='capture'&&arg&&second&&out)await capture(arg,second,out);
else if(mode==='verify'&&arg){const count=await verifyData(JSON.parse(await readFile(arg,'utf8')));console.log('Verified '+count+' matched triplets and full/focused executable equivalence.');}
else throw new Error('Usage: node scripts/presentation_cases.mjs capture BASE RULES NEW_OUTPUT | verify DATASET');
