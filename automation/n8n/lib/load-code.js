'use strict';
// Code-node sources live in code/*.js. Two consumers share this loader, so what is tested is what is deployed:
//   * build.js inlines the expanded source into the workflow JSON;
//   * the unit tests evaluate the same expanded source and use its module.exports.
const fs = require('node:fs');
const path = require('node:path');

const CODE_DIR = path.join(__dirname, '..', 'code');

function expandIncludes(source) {
  return source.replace(/^\/\/ @include (\S+)\s*$/gm, (_, file) => fs.readFileSync(path.join(CODE_DIR, file), 'utf8').trimEnd());
}

function codeSource(file) {
  return expandIncludes(fs.readFileSync(path.join(CODE_DIR, file), 'utf8'));
}

/**
 * Evaluate a Code-node source the way n8n would (minus the n8n globals) and return what it exports.
 * Same realm as the tests (a `new Function`, not a vm context) so returned objects compare equal to test literals.
 */
function loadCode(file, globals = {}) {
  const module = { exports: {} };
  const names = ['module', 'console', ...Object.keys(globals)];
  // a function body, exactly like n8n's Code node, so a top-level `return` (the n8n entry) is legal
  new Function(...names, codeSource(file))(module, console, ...Object.values(globals));
  return module.exports;
}

module.exports = { codeSource, loadCode, expandIncludes, CODE_DIR };
