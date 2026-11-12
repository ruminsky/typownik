// Self-check of the Betclic coupon reader in docs/index.html on real OCR output (node scripts/coupon_parse_test.mjs)
import fs from "fs";
const h=fs.readFileSync(new URL("../docs/index.html", import.meta.url),"utf8"); let js=h.slice(h.indexOf("<script>")+8,h.lastIndexOf("</script>")); js=js.slice(0,js.lastIndexOf("(async()=>{"));
const stub=`const DUMMY=new Proxy(function(){},{get:(t,k)=>k===Symbol.toPrimitive?()=>"":DUMMY,set:()=>true,apply:()=>DUMMY});
const document={querySelector:()=>DUMMY,querySelectorAll:()=>[],addEventListener(){},createElement:()=>DUMMY,head:DUMMY};const window={addEventListener(){},scrollTo(){}};
const localStorage={getItem:()=>null,setItem(){}};const location={hash:""};`;
const B="Betclic\nNiestety...\nzo\n9000 >\nAnglia\n© Wynik meczu (z wyłączeniem dogrywki) 1, 12\nDzisiaj 20:45\nAnglia 2-1 Chorwacja\nFrancja +83\n© wymi | ) 184\nWynik meczu (z wyłączeniem dogrywki) b;\nDzisiaj 20:45\nWłochy 3-1 Francja\nCzarnogóra +60\n© Wyni i 1,61\nWynik meczu (z wyłączeniem dogrywki) P\nDzisiaj 20:45\nCzarnogóra 2-1 Łotwa\nAlbania\n© Wynik meczu (z wyłączeniem dogrywki) 1,03\nDzisiaj 20:45\nAlbania 2-0 Finlandia\n=\nKurs łączny 3,38 3,42\nStawka 2,00 zł\nWygrane 0,00 zł\n[11/2026 21:5.\n";
const f=new Function("B",stub+js+`return [B].map(t=>{const c=parseCoupon(t);return c.legs.map(l=>[l.home,l.away,pickCode(l.pick,l.home,l.away),l.odds,l.hg,l.ag].join(" ")).concat(["total "+c.total+" stake "+c.stake+" won "+c.won+" date "+c.date])});`);
const [b]=f(B);
const want=["Anglia Chorwacja 1 1.12 2 1","Włochy Francja 2 1.84 3 1","Czarnogóra Łotwa 1 1.61 2 1","Albania Finlandia 1 1.03 2 0"];
if(JSON.stringify(b.slice(0,4))!==JSON.stringify(want)){ console.error("Błąd odczytu kuponu:", b); process.exit(1); } console.log("Odczyt kuponu: OK");
