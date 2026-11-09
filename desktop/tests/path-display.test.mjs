import test from 'node:test';
import assert from 'node:assert/strict';
import { displayPath } from '../src/path-display.js';

test('home abbreviation only replaces the home directory at a directory boundary', () => {
  assert.equal(displayPath('/Users/pc/Loops/loop-123', '/Users/pc'), '~/Loops/loop-123');
  assert.equal(displayPath('/Users/pc', '/Users/pc/'), '~');
  assert.equal(displayPath('/Users/pc-other/test', '/Users/pc'), '/Users/pc-other/test');
  assert.equal(displayPath('/tmp/Loop', '/Users/pc'), '/tmp/Loop');
});
