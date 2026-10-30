#!/usr/bin/env node
// Capture/replay exact outcomes of pinned Shoalrun functions. No model calls.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { stripTypeScriptTypes } from 'node:module';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const sha = value => createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(value, (_, v) => v && !Array.isArray(v) && typeof v === 'object'
  ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
const equal = (a, b) => canonical(a) === canonical(b);
const clone = value => JSON.parse(JSON.stringify(value));
const encodedResult = value => value === undefined ? { '$undefined': true } : clone(value);
async function loadImplementation(text) {
  const js = stripTypeScriptTypes(text, { mode: 'strip' });
  if (/^\s*import\s/m.test(js)) throw new Error('Unexpected runtime import: oracle must stay self-contained');
  return import('data:text/javascript;base64,' + Buffer.from(js).toString('base64'));
}
function execute(api, call) {
  if (!['validateRetrieval', 'embeddingProviderConfig', 'queryProviderConfig'].includes(call.function)) throw new Error('Unknown oracle function');
  try { return { ok: true, result: encodedResult(api[call.function](...call.args)) }; }
  catch (error) { return { ok: false, error: { name: error.name, message: error.message } }; }
}
function shuffled(values, salt) {
  return values.map((value, i) => ({ value, key: sha(salt + ':' + i) })).sort((a,b) => a.key.localeCompare(b.key)).map(x => x.value);
}
function options(values, salt) {
  return Object.fromEntries(shuffled(values, salt).map((value, i) => ['option_' + String.fromCharCode(97+i), value]));
}
function call(functionName, args) { return { function: functionName, args }; }
function visibleCalls(c) {
  return c.task === 'config-choice'
    ? Object.values(c.options).map(value => call(c.input.function, [value]))
    : [c.input];
}
function derive(caseData, outcomes) {
  if (caseData.task === 'config-validity') return outcomes[0].ok ? 'yes' : 'no';
  if (caseData.task === 'config-choice') {
    const accepted = outcomes.map((outcome, i) => outcome.ok ? Object.keys(caseData.options)[i] : null).filter(Boolean);
    if (accepted.length !== 1) throw new Error('Choice must have exactly one accepted config');
    return accepted[0];
  }
  if (!outcomes[0].ok) throw new Error('Selection helper unexpectedly threw');
  const result = outcomes[0].result;
  const pair = { provider: result.provider, model: result.model };
  const matches = Object.entries(caseData.options).filter(([, value]) => equal(value, pair));
  if (matches.length !== 1) throw new Error('Selection must have exactly one matching provider/model pair');
  return matches[0][0];
}
async function capture(source, output) {
  const rel = 'framework/src/retrieval-config.ts';
  const status = execFileSync('git', ['-C', source, 'status', '--porcelain', '--', rel], { encoding:'utf8' });
  if (status.trim()) throw new Error('Oracle source has local changes');
  const commit = execFileSync('git', ['-C', source, 'rev-parse', 'HEAD'], { encoding:'utf8' }).trim();
  const text = await readFile(path.join(source, rel), 'utf8');
  const api = await loadImplementation(text);
  const cases = [];
  function add(data, calls) {
    if (!equal(calls,visibleCalls(data))) throw new Error('Capture call/input mismatch');
    const outcomes = calls.map(c => execute(api, c));
    const expected = derive(data, outcomes);
    cases.push({ ...data, expected, oracle: { calls, outcomes }, rationale:'Expected label derived from recorded execution of the pinned function; replayable with scripts/known_answers.mjs verify.' });
  }
  const families = [
    ['provider', 'dev',
      ['voyage','openai',' voyage ',' openai '].map(provider => ({ provider, model:'m' })),
      ['cohere','VOYAGE',null,''].map(provider => ({ provider, model:'m' }))],
    ['dimension', 'test',
      [1,256,3,Number.MAX_SAFE_INTEGER].map(dimension => ({ provider:'voyage',model:'m',dimension })),
      [0,-1,1.5,'1024'].map(dimension => ({ provider:'voyage',model:'m',dimension }))],
    ['key-env', 'dev',
      ['MY_KEY','_KEY','key2',' A_B '].map(keyEnv => ({ provider:'voyage',model:'m',keyEnv })),
      ['1KEY','MY-KEY','has space',''].map(keyEnv => ({ provider:'voyage',model:'m',keyEnv }))],
    ['base-url', 'test',
      ['http://localhost:11434/v1','https://api.example.com/v1','http://127.0.0.1:8080',' https://example.com/path '].map(baseUrl => ({ provider:'openai',model:'m',baseUrl })),
      ['ftp://example.com','https://user:dummy@example.com','not a url',''].map(baseUrl => ({ provider:'openai',model:'m',baseUrl }))],
    ['unknown-fields', 'dev',
      [{provider:'voyage',model:'m'},{provider:'openai',model:'m',dimension:8},{provider:'voyage',model:'m',queryModel:'q'},{provider:'openai',model:'m',keyEnv:'LOCAL_KEY'}],
      ['flavor','endpoint','apiKey','extra'].map(key => ({provider:'voyage',model:'m',[key]:'dummy'}))],
    ['model-text', 'test',
      [{provider:'voyage',model:' m '},{provider:'openai',model:'m',queryModel:' q '},{provider:'voyage',model:'0'},{provider:'openai',model:'m',queryModel:'q2'}],
      [{provider:'voyage',model:''},{provider:'openai',model:42},{provider:'voyage',model:'m',queryModel:''},{provider:'openai',model:'m',queryModel:null}]],
  ];
  for (const [family, split, good, bad] of families) {
    const wrap = (entry, index) => ({embeddings:{...entry, ...(entry.model === 'm' ? {model:'m-'+sha(family+':'+index).slice(0,8)} : {})}});
    const accepted = good.map(wrap), rejected = bad.map(wrap);
    for (const [index, config] of [...accepted,...rejected].entries()) {
      add({id:`${family}-validity-${index+1}`,task:'config-validity',group:family,split,
           query:'Does validateRetrieval return normally without throwing for this input?',
           input:{function:'validateRetrieval',args:[config]}}, [call('validateRetrieval',[config])]);
    }
    for (let index=0;index<4;index++) {
      const opts=options([accepted[index],rejected[index],rejected[(index+1)%4],rejected[(index+2)%4]],family+index);
      add({id:`${family}-choice-${index+1}`,task:'config-choice',group:family,split,
           query:'Which one of these retrieval inputs does validateRetrieval accept without throwing?',options:opts,
           input:{function:'validateRetrieval'}}, Object.values(opts).map(config=>call('validateRetrieval',[config])));
    }
  }
  for (let i=0;i<12;i++) {
    const provider=i%2?'openai':'voyage';
    const alternate=provider==='openai'?'voyage':'openai';
    const baseModel='m-'+sha('base:'+i).slice(0,8), override='m-'+sha('override:'+i).slice(0,8), queryModel='m-'+sha('query:'+i).slice(0,8);
    const config=api.validateRetrieval({embeddings:{provider,model:baseModel,queryModel}});
    const flags=[{}, {model:override}, {provider:alternate,model:override}, {provider:alternate}][i%4];
    const opts=options([{provider,model:baseModel},{provider,model:override},{provider:alternate,model:override},{provider:alternate,model:baseModel}], 'embedding'+i);
    add({id:`embedding-selection-${i+1}`,task:'provider-selection',group:'embedding-overrides',split:'dev',
         query:'Which provider and model pair does embeddingProviderConfig return for this config and flags?',
         input:{function:'embeddingProviderConfig',args:[config,flags]},options:opts},[call('embeddingProviderConfig',[config,flags])]);
    const stored={provider:i%3===0?alternate:provider,model:baseModel,dimension:1024};
    const qflags=i%2?{model:override}:{};
    const pairs=[{provider:stored.provider,model:baseModel},{provider:stored.provider,model:queryModel},{provider:stored.provider,model:override},{provider:stored.provider===provider?alternate:provider,model:queryModel}];
    add({id:`query-selection-${i+1}`,task:'provider-selection',group:'query-overrides',split:'test',
         query:'Which provider and model pair does queryProviderConfig return for this config, stored metadata and flags?',
         input:{function:'queryProviderConfig',args:[config,stored,qflags]},options:options(pairs,'query'+i)},[call('queryProviderConfig',[config,stored,qflags])]);
  }
  const data={schema_version:2,name:'shoalrun-known-v1',
    label_origin:'Executed local Shoalrun functions. Expected answers are derived from recorded return values/throws, not prose sufficiency judgments.',
    scope:'Predict the exact pinned local function behavior. Acceptance does not establish full-project validity, provider support, model existence, credentials, or successful inference.',
    split_policy:'Mutation families and selection-helper families stay within one split. Both splits share the same supplied source contract. This is not an unseen-code benchmark.',
    reference:{text,sha256:sha(text),source:{repository:'https://github.com/shoalrun/shoalrun',commit,path:rel,license:'Apache-2.0'}},
    oracle_runtime:{node:process.version,platform:process.platform,serialization:'JSON; undefined return is {$undefined:true}; undefined object properties omitted; exceptions record name and message.'},cases};
  await mkdir(path.dirname(output),{recursive:true});
  await writeFile(output,JSON.stringify(data,null,2)+'\n',{flag:'wx'});
  console.log(`Captured ${cases.length} executed cases -> ${output}`);
}
async function verify(file) {
  const data=JSON.parse(await readFile(file,'utf8'));
  if (sha(data.reference.text)!==data.reference.sha256) throw new Error('Source hash mismatch');
  if (data.oracle_runtime.node!==process.version) throw new Error(`Replay needs ${data.oracle_runtime.node}; current ${process.version}`);
  const api=await loadImplementation(data.reference.text);
  for(const c of data.cases){
    const calls=visibleCalls(c);
    if(!equal(calls,c.oracle.calls))throw new Error('Model-visible input/options do not match oracle calls: '+c.id);
    const outcomes=calls.map(call=>execute(api,call));
    if(!equal(outcomes,c.oracle.outcomes))throw new Error('Oracle output mismatch: '+c.id);
    if(derive(c,outcomes)!==c.expected)throw new Error('Expected label mismatch: '+c.id);
  }
  console.log(`Verified ${data.cases.length} known answers by replaying pinned source on ${process.version}.`);
}
const [mode,arg,output]=process.argv.slice(2);
if(mode==='capture'&&arg&&output)await capture(path.resolve(arg),output);
else if(mode==='verify'&&arg)await verify(arg);
else throw new Error('Usage: node scripts/known_answers.mjs capture SHOALRUN_CHECKOUT NEW_OUTPUT | verify DATASET');
