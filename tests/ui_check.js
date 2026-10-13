// Optional UI check: node tests/ui_check.js <absolute path to playwright module>.
const {chromium} = require(process.argv[2] || 'playwright');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const server=spawn('python',['tests/ui_server.py'],{stdio:'pipe',windowsHide:true});
  let browser;
  try {
    for(let i=0;i<40;i++){try{const response=await fetch('http://127.0.0.1:8766/');if(response.ok)break;}catch{}await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({headless:true,channel:'msedge'});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto('http://127.0.0.1:8766/');
    await page.locator('[name=password]').fill('ui-test-password-only');
    await page.locator('#login-form button').click();
    await page.locator('#workspace').waitFor({state:'visible'});
    assert.match(await page.locator('#content').innerText(),/200,000.00/);
    fs.mkdirSync('data',{recursive:true});
    await page.screenshot({path:'data/ui-home.png',fullPage:true});
    for(const id of ['holdings','transactions','portfolio','research','notices','settings']){
      await page.locator(`#nav [data-id="${id}"]`).click();
      assert.ok((await page.locator('#content').innerText()).length>30);
    }
    await page.locator('#nav [data-id=holdings]').click();
    await page.getByRole('button',{name:'导入期初数据',exact:true}).click();
    await page.getByRole('button',{name:'＋ 期初现金',exact:true}).click();
    await page.locator('[name=batch]').fill('browser-opening');
    await page.locator('[name=date]').fill('2026-01-02');
    await page.locator('[data-field=amount]').fill('1000');
    await page.getByRole('button',{name:'保存草稿并检查',exact:true}).click();
    await page.locator('#confirm-batch').click();
    await page.locator('#modal').waitFor({state:'hidden'});
    assert.match(await page.locator('#content').innerText(),/81,000.00/);
    await page.locator('#quick-entry').click();
    await page.locator('[name=quantity]').fill('10');
    await page.locator('[name=price]').fill('100');
    await page.locator('[name=fee]').fill('5');
    await page.getByRole('button',{name:'预览变动',exact:true}).click();
    await page.locator('#confirm-entry').click();
    await page.locator('#modal').waitFor({state:'hidden'});
    assert.match(await page.locator('#content').innerText(),/79,995.00/);
    await page.getByRole('button',{name:'详情 ↗',exact:true}).click();
    assert.match(await page.locator('#modal').innerText(),/1010/);
    await page.locator('#modal-close').click();
    await page.locator('#nav [data-id=transactions]').click();
    await page.getByRole('button',{name:'修正',exact:true}).click();
    await page.locator('[name=quantity]').fill('8');
    await page.getByRole('button',{name:'保存草稿',exact:true}).click();
    await page.locator('#modal').waitFor({state:'hidden'});
    await page.getByRole('button',{name:'继续录入',exact:true}).click();
    await page.locator('[name=reason]').fill('实际成交为8股');
    await page.getByRole('button',{name:'预览变动',exact:true}).click();
    await page.locator('#confirm-entry').click();
    await page.locator('#modal').waitFor({state:'hidden'});
    await page.locator('#nav [data-id=holdings]').click();
    assert.match(await page.locator('#content').innerText(),/80,195.00/);
    await page.locator('#nav [data-id=research]').click();
    await page.getByRole('button',{name:'＋ 发起研究',exact:true}).click();
    await page.getByRole('button',{name:'创建任务',exact:true}).click();
    await page.locator('#modal').waitFor({state:'hidden'});
    assert.match(await page.locator('#content').innerText(),/排队/);
    await page.setViewportSize({width:390,height:844});
    await page.locator('#nav [data-id=home]').click();
    await page.screenshot({path:'data/ui-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log('PASS: login, seven pages, opening draft/confirm, trade preview/confirm, detail, correction draft/resume, research queue, mobile, no browser errors');
  } finally {if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
