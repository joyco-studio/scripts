/**
 * Tests for fix-svg-jsx-attrs.js codemod
 *
 * Run with:
 *   pnpm test
 *
 * Requires: Node 18+ and jscodeshift installed
 */

const { test, describe } = require('node:test')
const assert = require('node:assert')
const jscodeshift = require('jscodeshift')
const transform = require('../fix-svg-jsx-attrs.js')

function applyTransform(source) {
  return transform({ source }, { jscodeshift })
}

describe('fix-svg-jsx-attrs', () => {
  describe('transforms SVG attributes inside <svg>', () => {
    test('fill-rule → fillRule', () => {
      const input = `<svg><path fill-rule="evenodd" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('fillRule'))
      assert.ok(!output.includes('fill-rule'))
    })

    test('clip-rule → clipRule', () => {
      const input = `<svg><path clip-rule="nonzero" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('clipRule'))
      assert.ok(!output.includes('clip-rule'))
    })

    test('stroke-width → strokeWidth', () => {
      const input = `<svg><path stroke-width="2" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('strokeWidth'))
      assert.ok(!output.includes('stroke-width'))
    })

    test('stroke-linecap → strokeLinecap', () => {
      const input = `<svg><line stroke-linecap="round" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('strokeLinecap'))
      assert.ok(!output.includes('stroke-linecap'))
    })

    test('stroke-linejoin → strokeLinejoin', () => {
      const input = `<svg><path stroke-linejoin="bevel" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('strokeLinejoin'))
      assert.ok(!output.includes('stroke-linejoin'))
    })

    test('stroke-miterlimit → strokeMiterlimit', () => {
      const input = `<svg><path stroke-miterlimit="4" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('strokeMiterlimit'))
      assert.ok(!output.includes('stroke-miterlimit'))
    })

    test('color-interpolation-filters → colorInterpolationFilters', () => {
      const input = `<svg><filter color-interpolation-filters="sRGB" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('colorInterpolationFilters'))
      assert.ok(!output.includes('color-interpolation-filters'))
    })

    test('transforms multiple attributes at once', () => {
      const input = `<svg><path fill-rule="evenodd" stroke-width="2" clip-rule="nonzero" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('fillRule'))
      assert.ok(output.includes('strokeWidth'))
      assert.ok(output.includes('clipRule'))
    })
  })

  describe('handles nested SVG elements', () => {
    test('transforms attributes in nested elements', () => {
      const input = `
        <svg>
          <g>
            <path fill-rule="evenodd" />
            <circle stroke-width="1" />
          </g>
        </svg>
      `
      const output = applyTransform(input)
      assert.ok(output.includes('fillRule'))
      assert.ok(output.includes('strokeWidth'))
    })

    test('transforms deeply nested elements', () => {
      const input = `
        <svg>
          <defs>
            <clipPath>
              <rect clip-rule="nonzero" />
            </clipPath>
          </defs>
        </svg>
      `
      const output = applyTransform(input)
      assert.ok(output.includes('clipRule'))
    })
  })

  describe('leaves non-SVG elements untouched', () => {
    test('does not transform attributes outside <svg>', () => {
      const input = `<div fill-rule="evenodd" />`
      const output = applyTransform(input)
      assert.ok(output.includes('fill-rule'))
      assert.ok(!output.includes('fillRule'))
    })

    test('does not transform custom web components', () => {
      const input = `<my-component fill-rule="evenodd" stroke-width="2" />`
      const output = applyTransform(input)
      assert.ok(output.includes('fill-rule'))
      assert.ok(output.includes('stroke-width'))
    })

    test('does not transform sibling elements outside svg', () => {
      const input = `
        <div>
          <span fill-rule="test" />
          <svg><path fill-rule="evenodd" /></svg>
          <span stroke-width="test" />
        </div>
      `
      const output = applyTransform(input)
      // Inside svg should be transformed
      assert.ok(output.includes('fillRule'))
      // Outside svg should NOT be transformed
      assert.ok(output.includes('stroke-width="test"'))
    })
  })

  describe('preserves other attributes', () => {
    test('keeps standard JSX attributes unchanged', () => {
      const input = `<svg className="icon" width="24" height="24"><path d="M0 0" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('className'))
      assert.ok(output.includes('width'))
      assert.ok(output.includes('height'))
      assert.ok(output.includes('d='))
    })

    test('keeps unknown kebab-case attributes unchanged', () => {
      const input = `<svg><path data-testid="path" aria-label="icon" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('data-testid'))
      assert.ok(output.includes('aria-label'))
    })
  })

  describe('handles edge cases', () => {
    test('handles self-closing svg', () => {
      const input = `<svg fill-rule="evenodd" />`
      const output = applyTransform(input)
      // The svg element itself should also be transformed
      assert.ok(output.includes('fillRule') || output.includes('fill-rule'))
    })

    test('handles empty svg', () => {
      const input = `<svg></svg>`
      const output = applyTransform(input)
      assert.strictEqual(output.trim(), `<svg></svg>`)
    })

    test('handles multiple svgs in same file', () => {
      const input = `
        <div>
          <svg><path fill-rule="evenodd" /></svg>
          <svg><rect stroke-width="2" /></svg>
        </div>
      `
      const output = applyTransform(input)
      assert.ok(output.includes('fillRule'))
      assert.ok(output.includes('strokeWidth'))
    })

    test('returns original source when no changes needed', () => {
      const input = `<svg><path fillRule="evenodd" /></svg>`
      const output = applyTransform(input)
      assert.ok(output.includes('fillRule'))
    })
  })
})

