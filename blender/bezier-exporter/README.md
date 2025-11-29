# Blender Bezier2JSON Exporter

## Acknowledgements
First of all, note that this script is strongly inspired on @qerrant's [BezierBlenderToUE](https://github.com/qerrant/BezierBlenderToUE) script.
We adapted it to export `json` instead of `csv`.

## Why use this exporter?
This plug-in raises from the necessity of avoiding to handcraft bezier curves, mostly when they are related to some other geometry. Why can't we just export them as we do with our `gltf` models? Remember that `gltf` doesn't support bezier data like points + handles info.

There's where this plug-in comes very useful. Imagine you need a spline that travels through a castle model, to animate the camera position for example. Crafting
that by hand might be a pain in the ass 😬. Now imagine that, for some reason, you update your castle model, remove some rooms here and there and scale some stuff, you have go back and update all the points by hand (again) to keep it consistent with the model. There's no reason to do that if you can just update it on Blender working on the same context 🙏🏼.

## Installation
To use this plug-in just install it like any other custom Blender plug-in, it will add a new export type under `File > Export > Bezier2JSON (.json)`.
If you don't know how to install a custom plug-in [check this](https://youtu.be/cyt0O7saU4Q?t=33).

## How to use it (ThreeJS)
Once you have your `bezier.json` there's a piece of code that you'll need to parse into ThreeJS classes. You can get it in this example's code.

```typescript
type Curve = {
  id: number
  px: number
  py: number
  pz: number
  hlx: number
  hly: number
  hlz: number
  hrx: number
  hry: number
  hrz: number
}

/* 
  This works like:
  [..points, controls..]
                        -----> CubicBezierCurve3
  [..points, controls..]
                        -----> CubicBezierCurve3
  [..points, controls..]
                        -----> CubicBezierCurve3
  [..points, controls..]

  Returns an array of connected bezier curves you
  can then add to a CurvePath to get a path.
*/
export const getBezierCurves = (curve: Curve[], scale = 1) => {
  const beziers = []

  for (let i = 0; i < curve.length; i += 1) {
    const p1 = curve[i]
    const p2 = curve[i + 1]

    if (!p2) break

    beziers.push(
      new THREE.CubicBezierCurve3(
        new THREE.Vector3(p1.px, p1.pz, p1.py).multiplyScalar(scale),
        new THREE.Vector3(p1.hrx, p1.hrz, p1.hry).multiplyScalar(scale),
        new THREE.Vector3(p2.hlx, p2.hlz, p2.hly).multiplyScalar(scale),
        new THREE.Vector3(p2.px, p2.pz, p2.py).multiplyScalar(scale)
      )
    )
  }

  return beziers
}
```
