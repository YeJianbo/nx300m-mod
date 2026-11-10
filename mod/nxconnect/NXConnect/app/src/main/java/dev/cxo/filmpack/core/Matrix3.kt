package dev.cxo.filmpack.core

import kotlin.math.abs

/** Immutable row-major 3x3 matrix acting on column RGB vectors.
 * Double reference arithmetic; no color-space label, normalization or clamping is implicit.
 */
class Matrix3(rowMajor: DoubleArray) {
    private val m=rowMajor.copyOf()
    init { require(m.size==9 && m.all { it.isFinite() }) }

    fun values(): DoubleArray = m.copyOf()

    /** Safe for in-place vector use because the three input arguments are values. */
    fun transform(r: Double, g: Double, b: Double, out: DoubleArray, offset: Int = 0) {
        require(r.isFinite() && g.isFinite() && b.isFinite())
        require(offset >= 0 && offset <= out.size-3)
        out[offset]=m[0]*r+m[1]*g+m[2]*b
        out[offset+1]=m[3]*r+m[4]*g+m[5]*b
        out[offset+2]=m[6]*r+m[7]*g+m[8]*b
    }

    /** this * right; right acts first when applied to a column vector. */
    operator fun times(right: Matrix3): Matrix3 {
        val v=DoubleArray(9)
        for (r in 0..2) for (c in 0..2)
            v[r*3+c]=m[r*3]*right.m[c]+m[r*3+1]*right.m[3+c]+m[r*3+2]*right.m[6+c]
        return Matrix3(v)
    }

    /** Explicit absolute determinant guard, not a claim of the native guard's exact threshold.
     * Callers must select a suitable threshold for their matrix scale.
     */
    fun inverse(minAbsDeterminant: Double = 1E-12): Matrix3 {
        require(minAbsDeterminant.isFinite() && minAbsDeterminant > 0.0)
        val a=m[0]; val b=m[1]; val c=m[2]
        val d=m[3]; val e=m[4]; val f=m[5]
        val g=m[6]; val h=m[7]; val i=m[8]
        val A=e*i-f*h; val B=c*h-b*i; val C=b*f-c*e
        val D=f*g-d*i; val E=a*i-c*g; val F=c*d-a*f
        val G=d*h-e*g; val H=b*g-a*h; val I=a*e-b*d
        val det=a*A+b*D+c*G
        require(det.isFinite() && abs(det)>=minAbsDeterminant) { "Singular or guarded near-singular matrix" }
        return Matrix3(doubleArrayOf(A/det,B/det,C/det,D/det,E/det,F/det,G/det,H/det,I/det))
    }
}
