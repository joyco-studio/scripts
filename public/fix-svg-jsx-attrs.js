const SVG_ATTR_MAP = {
  // Fill and stroke attributes
  'fill-rule': 'fillRule',
  'fill-opacity': 'fillOpacity',
  'stroke-width': 'strokeWidth',
  'stroke-linecap': 'strokeLinecap',
  'stroke-linejoin': 'strokeLinejoin',
  'stroke-miterlimit': 'strokeMiterlimit',
  'stroke-dasharray': 'strokeDasharray',
  'stroke-dashoffset': 'strokeDashoffset',
  'stroke-opacity': 'strokeOpacity',
  
  // Clipping and masking
  'clip-rule': 'clipRule',
  'clip-path': 'clipPath',
  'mask-type': 'maskType',
  'mask-units': 'maskUnits',
  'mask-content-units': 'maskContentUnits',
  
  // Color and filters
  'color-interpolation': 'colorInterpolation',
  'color-interpolation-filters': 'colorInterpolationFilters',
  'color-profile': 'colorProfile',
  'color-rendering': 'colorRendering',
  'flood-color': 'floodColor',
  'flood-opacity': 'floodOpacity',
  'lighting-color': 'lightingColor',
  'stop-color': 'stopColor',
  'stop-opacity': 'stopOpacity',
  
  // Text attributes
  'font-family': 'fontFamily',
  'font-size': 'fontSize',
  'font-size-adjust': 'fontSizeAdjust',
  'font-stretch': 'fontStretch',
  'font-style': 'fontStyle',
  'font-variant': 'fontVariant',
  'font-weight': 'fontWeight',
  'text-anchor': 'textAnchor',
  'text-decoration': 'textDecoration',
  'text-rendering': 'textRendering',
  'unicode-bidi': 'unicodeBidi',
  'writing-mode': 'writingMode',
  'letter-spacing': 'letterSpacing',
  'word-spacing': 'wordSpacing',
  'text-length': 'textLength',
  'length-adjust': 'lengthAdjust',
  
  // Gradient and pattern attributes
  'gradient-units': 'gradientUnits',
  'gradient-transform': 'gradientTransform',
  'pattern-units': 'patternUnits',
  'pattern-content-units': 'patternContentUnits',
  'pattern-transform': 'patternTransform',
  'spread-method': 'spreadMethod',
  
  // Animation attributes
  'animation-name': 'animationName',
  'animation-duration': 'animationDuration',
  'animation-timing-function': 'animationTimingFunction',
  'animation-delay': 'animationDelay',
  'animation-iteration-count': 'animationIterationCount',
  'animation-direction': 'animationDirection',
  'animation-fill-mode': 'animationFillMode',
  'animation-play-state': 'animationPlayState',
  
  // Marker attributes
  'marker-start': 'markerStart',
  'marker-mid': 'markerMid',
  'marker-end': 'markerEnd',
  'marker-units': 'markerUnits',
  'marker-width': 'markerWidth',
  'marker-height': 'markerHeight',
  'ref-x': 'refX',
  'ref-y': 'refY',
  
  // Filter attributes
  'filter-units': 'filterUnits',
  'primitive-units': 'primitiveUnits',
  
  // Viewbox and coordinate system
  'view-box': 'viewBox',
  'preserve-aspect-ratio': 'preserveAspectRatio',
  
  // Miscellaneous
  'shape-rendering': 'shapeRendering',
  'image-rendering': 'imageRendering',
  'pointer-events': 'pointerEvents',
  'vector-effect': 'vectorEffect',
  'baseline-shift': 'baselineShift',
  'dominant-baseline': 'dominantBaseline',
  'glyph-orientation-horizontal': 'glyphOrientationHorizontal',
  'glyph-orientation-vertical': 'glyphOrientationVertical',
  'text-decoration': 'textDecoration',
  'enable-background': 'enableBackground',
}

module.exports = function transformer(file, api) {
  const j = api.jscodeshift
  const root = j(file.source)

  function isSvgElement(path) {
    return (
      path.node.openingElement.name.type === 'JSXIdentifier' &&
      path.node.openingElement.name.name === 'svg'
    )
  }

  function renameAttrs(path) {
    path.node.openingElement.attributes.forEach(attr => {
      if (
        attr.type === 'JSXAttribute' &&
        attr.name &&
        SVG_ATTR_MAP[attr.name.name]
      ) {
        attr.name.name = SVG_ATTR_MAP[attr.name.name]
      }
    })
  }

  root
    .find(j.JSXElement)
    .filter(isSvgElement)
    .forEach(svgPath => {
      j(svgPath)
        .find(j.JSXElement)
        .forEach(renameAttrs)
    })

  return root.toSource({ quote: 'single' })
}
