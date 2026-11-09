import test from 'node:test';
import assert from 'node:assert/strict';
import { requestError } from '../src/request-error.js';

test('native IPC failures display the application message without Electron internals', () => {
  const message = '无法连接对方 Loops，请确认对方已开启共享，并检查地址和网络。';
  assert.equal(requestError(new Error(`Error invoking remote method 'loops:request': Error: ${message}`)).message, message);
  for (const message of ['对方身份已变化，请重新申请连接并核对配对码。', 'Error: user script failed']) {
    assert.equal(requestError(new Error(message)).message, message);
  }
});
