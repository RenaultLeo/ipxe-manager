import fs from "fs";
const en = JSON.parse(fs.readFileSync("app/locale_values/_en.list.json", "utf8"));
for (const c of ["de", "es", "it", "pt"]) {
  fs.writeFileSync(`app/locale_values/${c}.list.json`, JSON.stringify(en));
}
console.log("cloned", en.length, "to de,es,it,pt");
