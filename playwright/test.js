// test.js
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
const assert = require('assert');

// Parse CLI args (e.g. "--video=run1.webm")
const args = process.argv.slice(2);
let videoFile = null;

for (const arg of args) {
  if (arg.startsWith('--video=')) {
    videoFile = arg.split('=')[1];
  }
}

(async () => {
  const browser = await chromium.launch();

  // If we want video, set up a directory for it
  let context;
  let page;

  if (videoFile) {
    const videosDir = path.join(__dirname, 'videos');
    if (!fs.existsSync(videosDir)) {
      fs.mkdirSync(videosDir, { recursive: true });
    }

    context = await browser.newContext({
      recordVideo: { dir: videosDir }
    });
  } else {
    context = await browser.newContext();
  }

  page = await context.newPage();

  // Load the local HTML file
  const filePath = path.join(__dirname, 'index.html');
  await page.goto('file://' + filePath);
  await page.waitForTimeout(300);

  // --- Test logic ---
  const initialText = await page.textContent('#count');
  assert.strictEqual(initialText, '0', 'Initial count should be 0');

  await page.click('#increment');

  const updatedText = await page.textContent('#count');
  assert.strictEqual(updatedText, '1', 'Count after one click should be 1');

  console.log('✅ Test passed!');

  // --- Video handling ---
  if (videoFile) {
    // Ensure final state is visible
    await page.waitForTimeout(500);

    // Get the video object while the page still exists
    const video = await page.video();

    // Closing the context flushes the video to disk
    await context.close();
    await browser.close();

    if (video) {
      const savedPath = await video.path();
      const destination = path.join(__dirname, videoFile);
      fs.renameSync(savedPath, destination);
      console.log(`🎥 Video saved to: ${destination}`);
    }
  } else {
    await context.close();
    await browser.close();
  }
})().catch(err => {
  console.error('❌ Test failed:', err);
  process.exit(1);
});
