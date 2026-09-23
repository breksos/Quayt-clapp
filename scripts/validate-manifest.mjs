import { readFile } from "node:fs/promises";
const manifest=JSON.parse(await readFile(new URL("../clatch.json",import.meta.url),"utf8"));
const expected=["status","show","hide","quit","ping"];
const actual=manifest.connector?.commands?.map(({name})=>name);
const checks=[[manifest.manifestVersion===1,"manifestVersion must be 1"],[manifest.protocol===2,"protocol must be 2"],[manifest.id==="com.arfium.quayt","id must be com.arfium.quayt"],[manifest.connector?.cli==="quayt","connector.cli must be quayt"],[manifest.connector?.cliBin==="bin/quayt","connector.cliBin must be bin/quayt"],[JSON.stringify(actual)===JSON.stringify(expected),"commands must be exactly status, show, hide, quit, ping"],[Array.isArray(manifest.connector?.signals)&&manifest.connector.signals.length===0,"signals must be empty"]];
const failures=checks.filter(([ok])=>!ok).map(([,message])=>message);
if(failures.length){console.error(failures.join("\n"));process.exit(1)}
console.log("clatch.json: Phase 1 manifest contract valid");
