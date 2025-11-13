// counter.spec.js
const { test, expect } = require('@playwright/test');
const path = require('path');

test('counter increments once', async ({ page }) => {
  await page.goto('file://' + path.join(__dirname, 'index.html'));

  await expect(page.locator('#count')).toHaveText('0');
  await page.waitForTimeout(300);

  await page.click('#increment');

  await expect(page.locator('#count')).toHaveText('1');
  await page.waitForTimeout(300); // To improve video output
});
