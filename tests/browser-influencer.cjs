// Local-only browser checks. All data and upload responses are controlled fixtures.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const root=path.resolve(__dirname,'..');
const fixture=Array.from({length:53},(_,i)=>({asin:`B${String(i).padStart(9,'0')}`,title:i===0?'<img src=x onerror=alert(1)>':'Cordless drill '+i,
  brand:'Example',category:i%2?'Tools':'Home',price:50+i,commission:15,monthly_sold:110+i,monthly_sold_90:100,growth:10+i,
  bsr:1000+i,bsr90:50,merchant_video:true,total_videos:2,influencer_videos:1,budget_remaining:5000,available_slots:20,
  score:50+i,qualified:i<51,valid_until:Date.now()/1000+3600,qualifying_campaign_count:2,
  failed_filters:i<51?[]:['sales_growth']}));
const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  res.setHeader('Content-Type',pathname.endsWith('.json')?'application/json':pathname.endsWith('.js')?'text/javascript':pathname.endsWith('.css')?'text/css':'text/html');
  if(pathname==='/data/influencer/status.json')return res.end(JSON.stringify({phase:'complete',pages:['page-0000.json'],counts:{evaluated:53,unavailable_sales_trend:2},selected:53,updated_at:new Date().toISOString(),tokens_consumed:120,tokens_left:80}));
  if(pathname==='/data/influencer/page-0000.json')return res.end(JSON.stringify(fixture));
  if(pathname.startsWith('/data/influencer/details/'))return res.end(JSON.stringify({campaigns:[{name:'Fixture campaign',commission:15,brand:'Example',start:'2026-01-01',end:'2030-01-01',budget:10000,budget_remaining:5000,available_slots:20,total_slots:100,recommended:true}]}));
  const file=path.resolve(root,'.'+pathname);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end('Not found');}
  fs.createReadStream(file).pipe(res);
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const executable=chromium.executablePath();
  const browser=await chromium.launch(fs.existsSync(executable)?{headless:true}:{channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const uploads=[];
    await page.route('https://script.google.com/**',async route=>{
      const fields=new URLSearchParams(route.request().postData());
      const csv=Buffer.from(fields.get('csvBase64'),'base64');uploads.push(csv);
      assert(csv.length<=2*1024*1024);
      await route.fulfill({contentType:'text/html',body:`<script>window.top.postMessage(${JSON.stringify({requestId:fields.get('requestId'),ok:true})},'*')</script>`});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/influencer-video-opportunities.html`);
    await page.waitForFunction(()=>document.getElementById('qualifiedCount').textContent==='51');
    assert.equal(await page.locator('#resultBody tr').count(),50);
    await page.getByRole('button',{name:'Next',exact:true}).click();assert.equal(await page.locator('#resultBody tr').count(),1);
    await page.locator('#showExcluded').check();assert.equal(await page.locator('#resultCount').textContent(),'53 matching products · including diagnostic rows');
    await page.locator('#growth-min').fill('60');assert.equal(await page.locator('#resultBody tr').count(),3);
    await page.getByRole('button',{name:'Reset filters'}).click();
    await page.locator('#categoryFilter').selectOption('Tools');assert.equal(await page.locator('#resultBody tr').count(),25);
    await page.getByRole('button',{name:'Reset filters'}).click();
    await page.locator('#opportunitySearch').fill('B000000000');assert.equal(await page.locator('#resultBody img').count(),0);
    assert.match(await page.locator('#resultBody').textContent(),/<img src=x/);
    await page.getByRole('button',{name:'2 campaign(s)'}).click();await page.getByText('Fixture campaign',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Close',exact:true}).click();
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'Export filtered CSV'}).click();assert.equal((await download).suggestedFilename(),'influencer-opportunities.csv');
    const header='Campaign Id,Campaign Name,ASIN List\n';
    const data=header+Array.from({length:18000},(_,i)=>`${i},"${'x'.repeat(110)}\nquoted, text",B000000001\n`).join('');
    await page.locator('#creatorCsvFile').setInputFiles([{name:'one.csv',mimeType:'text/csv',buffer:Buffer.from(data)},{name:'two.csv',mimeType:'text/csv',buffer:Buffer.from(header+'last,"Multiline\nname",B000000002\n')}]);
    await page.getByRole('button',{name:'Upload CSVs',exact:true}).click();
    await page.waitForFunction(()=>document.getElementById('creatorCsvUploadStatus').textContent.includes('CSV parts confirmed'),{},{timeout:60000});
    assert.equal(uploads.length,3);assert(uploads.every(x=>x.toString().startsWith(header)));
    const combined=uploads.slice(0,2).map(x=>x.toString().slice(header.length)).join('');assert.equal(combined,data.slice(header.length));
    await page.getByRole('button',{name:'Reset filters'}).click();
    await page.locator('.table-scroll').evaluate(el=>{el.scrollLeft=0;el.scrollTop=0;});
    fs.mkdirSync(path.join(root,'.test-output'),{recursive:true});
    await page.screenshot({path:path.join(root,'.test-output','opportunities-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    await page.screenshot({path:path.join(root,'.test-output','opportunities-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Browser checks passed: pagination, qualification, ranges, category, safe text, details, export, bounded multiline upload, mobile layout; no page errors.');
  } finally {await browser.close();server.close();}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
