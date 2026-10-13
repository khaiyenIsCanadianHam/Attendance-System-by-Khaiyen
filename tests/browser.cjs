const {chromium} = require(process.env.ATTENDANCE_PLAYWRIGHT || 'playwright');
const fs = require('fs');
const assert = require('node:assert/strict');
(async () => {
    const fixture = JSON.parse(fs.readFileSync(process.env.ATTENDANCE_BROWSER_FIXTURE, 'utf8'));
    const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true,
        args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
    try {
        const context = await browser.newContext({permissions:['camera']});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.goto(fixture.base);
        await page.locator('#username').fill('browserteacher');
        await page.locator('#password').fill(fixture.password);
        await Promise.all([page.waitForURL('**/dashboard'), page.getByRole('button',{name:'Sign in',exact:true}).click()]);
        await page.waitForFunction(() => typeof QrCamera === 'function' && typeof jsQR === 'function');
        assert.equal(await page.locator('#reader').count(), 1);
        // Exercise the real decoder with the generated authenticated QR images.
        let decoded;
        for (const [index, qrpath] of fixture.qrpaths.entries()) {
        decoded = await page.evaluate(async encoded => {
            const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
            const image = await createImageBitmap(new Blob([bytes],{type:'image/png'}));
            const canvas = document.createElement('canvas');
            canvas.width = image.width; canvas.height = image.height;
            const context = canvas.getContext('2d');
            context.drawImage(image,0,0);
            const pixels = context.getImageData(0,0,canvas.width,canvas.height);
            const result = jsQR(pixels.data,canvas.width,canvas.height);
            if (!result) throw new Error('QR did not decode');
            return result.data;
        },fs.readFileSync(qrpath).toString('base64'));
        assert.equal(decoded,fixture.qrtokens[index]);
        }
        fixture.qr = decoded;
        // Start the real camera with Chromium's fake device; capture its callback
        // so rejection/recovery can be tested without a physical student camera.
        await page.evaluate(() => {
            const original = QrCamera.prototype.start;
            QrCamera.prototype.start = function() {
                window.testScan = this.onDecode;
                return original.call(this);
            };
        });
        await page.getByRole('button',{name:'Start scanner'}).click();
        await page.waitForFunction(() => document.getElementById('status').textContent.startsWith('Scanner ready'));
        await page.evaluate(() => window.testScan('invalid-qr'));
        await page.waitForFunction(() => document.getElementById('status').textContent === 'Invalid QR code');
        await page.waitForTimeout(1300);
        await page.evaluate(qr => window.testScan(qr),fixture.qr);
        await page.waitForFunction(() => document.querySelectorAll('#attendanceList tr[data-record]').length === 1);
        assert.equal(await page.locator('#count').textContent(),'1');
        assert.equal(await page.locator('#attendanceList img').count(),0);
        assert.equal(await page.evaluate(() => window.pwned),undefined);
        assert.match(await page.locator('#attendanceList').textContent(),/<img/);
        await page.evaluate(() => { window.testTrack = document.querySelector('#reader video').srcObject.getTracks()[0]; });
        await page.getByRole('button',{name:'Stop scanner'}).click();
        assert.equal(await page.evaluate(() => window.testTrack.readyState),'ended');
        await page.setViewportSize({width:1280,height:850});
        if (process.env.HANAMI_SCREENSHOTS) await page.screenshot({path:process.env.HANAMI_SCREENSHOTS+'/desktop.png',fullPage:true});
        await page.setViewportSize({width:390,height:844});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
        if (process.env.HANAMI_SCREENSHOTS) await page.screenshot({path:process.env.HANAMI_SCREENSHOTS+'/mobile.png',fullPage:true});
        await page.reload();
        await page.waitForFunction(() => document.getElementById('count').textContent === '1');
        await page.locator('#attendanceList').getByRole('button').click();
        await page.waitForFunction(() => document.getElementById('count').textContent === '0');
        const download = page.waitForEvent('download');
        await page.getByRole('button',{name:'Download CSV'}).click();
        assert.equal((await download).suggestedFilename(),'hanami-attendance.csv');
        await page.getByRole('button',{name:'Sign out',exact:true}).click();
        await page.waitForURL(fixture.base + '/');
        assert.deepEqual(errors,[]);
        console.log('Browser passed: login, 20 real QR decodes, camera startup, invalid-scan recovery, XSS-safe rendering, reload, timeout, CSV download, logout.');
    } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
