import fs from "fs";
const t = fs.readFileSync("app/i18n.py", "utf8");
const start = t.indexOf('"en": {');
if (start < 0) throw new Error("no en block");
let i = t.indexOf("{", start);
let depth = 0;
let end = -1;
for (let k = i; k < t.length; k++) {
  const c = t[k];
  if (c === "{") depth++;
  else if (c === "}") {
    depth--;
    if (depth === 0) {
      end = k;
      break;
    }
  }
}
const block = t.slice(i, end + 1);
// crude: eval as JS object (keys are quoted strings — valid JS object literal)
const en = eval("(" + block + ")");
const vals = Object.values(en);
fs.mkdirSync("app/locale_values", { recursive: true });
fs.writeFileSync("app/locale_values/_en.list.json", JSON.stringify(vals), "utf8");
const uniq = [...new Set(vals)].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
fs.writeFileSync("tools/_unique_en_strings.json", JSON.stringify(uniq), "utf8");
console.log("values", vals.length, "unique", uniq.length);
