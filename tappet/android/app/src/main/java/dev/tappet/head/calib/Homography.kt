package dev.tappet.head.calib

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.sqrt

/** 2D point. */
data class P2(val x: Double, val y: Double)

/**
 * 3×3 planar homography (row-major, [h] has 9 entries) mapping (x,y) → (x',y').
 * Fitted with the normalized Direct Linear Transform: Hartley-normalize both point sets, build the
 * 2N×9 DLT system, take the eigenvector of AᵀA with the smallest eigenvalue (Jacobi), denormalize.
 * With exactly 4 points this is the exact solution; with more it is the algebraic least-squares fit.
 */
class Homography(val h: DoubleArray) {
    init { require(h.size == 9) }

    fun apply(x: Double, y: Double): P2 {
        val w = h[6] * x + h[7] * y + h[8]
        require(abs(w) > 1e-12) { "point maps to infinity" }
        return P2((h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w)
    }

    fun apply(p: P2) = apply(p.x, p.y)

    fun inverse(): Homography {
        val m = h
        val a = m[4] * m[8] - m[5] * m[7]
        val b = -(m[3] * m[8] - m[5] * m[6])
        val c = m[3] * m[7] - m[4] * m[6]
        val det = m[0] * a + m[1] * b + m[2] * c
        require(abs(det) > 1e-15) { "singular homography" }
        val inv = doubleArrayOf(
            a, -(m[1] * m[8] - m[2] * m[7]), m[1] * m[5] - m[2] * m[4],
            b, m[0] * m[8] - m[2] * m[6], -(m[0] * m[5] - m[2] * m[3]),
            c, -(m[0] * m[7] - m[1] * m[6]), m[0] * m[4] - m[1] * m[3],
        ).map { it / det }.toDoubleArray()
        return Homography(inv).normalized()
    }

    /** Scale so h[8] == 1 when possible (canonical form for storage). */
    fun normalized(): Homography {
        val s = if (abs(h[8]) > 1e-12) h[8] else sqrt(h.sumOf { it * it })
        return Homography(h.map { it / s }.toDoubleArray())
    }

    /** Composition: (this ∘ other)(p) = this(other(p)). */
    fun compose(other: Homography): Homography {
        val a = h
        val b = other.h
        val r = DoubleArray(9)
        for (i in 0..2) for (j in 0..2) {
            var s = 0.0
            for (k in 0..2) s += a[i * 3 + k] * b[k * 3 + j]
            r[i * 3 + j] = s
        }
        return Homography(r).normalized()
    }

    /** RMS and max reprojection error of src→dst. */
    fun residuals(src: List<P2>, dst: List<P2>): Pair<Double, Double> {
        var sum = 0.0
        var mx = 0.0
        for (i in src.indices) {
            val p = apply(src[i])
            val e = hypot(p.x - dst[i].x, p.y - dst[i].y)
            sum += e * e
            mx = maxOf(mx, e)
        }
        return Pair(sqrt(sum / src.size), mx)
    }

    override fun toString() = h.joinToString(prefix = "H[", postfix = "]") { "%.6g".format(it) }

    companion object {
        fun identity() = Homography(doubleArrayOf(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))

        /** Hartley normalization: translate centroid to origin, scale mean distance to √2. */
        private fun normalizer(pts: List<P2>): DoubleArray {
            val cx = pts.sumOf { it.x } / pts.size
            val cy = pts.sumOf { it.y } / pts.size
            val md = pts.sumOf { hypot(it.x - cx, it.y - cy) } / pts.size
            val s = if (md > 1e-12) sqrt(2.0) / md else 1.0
            return doubleArrayOf(s, 0.0, -s * cx, 0.0, s, -s * cy, 0.0, 0.0, 1.0)
        }

        fun fit(src: List<P2>, dst: List<P2>): Homography {
            require(src.size == dst.size) { "point count mismatch" }
            require(src.size >= 4) { "need at least 4 point pairs" }
            val tS = Homography(normalizer(src))
            val tD = Homography(normalizer(dst))
            val ns = src.map { tS.apply(it) }
            val nd = dst.map { tD.apply(it) }
            val ata = Array(9) { DoubleArray(9) }
            fun addRow(r: DoubleArray) {
                for (i in 0..8) for (j in 0..8) ata[i][j] += r[i] * r[j]
            }
            for (i in ns.indices) {
                val (x, y) = ns[i]
                val (u, v) = nd[i]
                addRow(doubleArrayOf(-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u))
                addRow(doubleArrayOf(0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v))
            }
            val (vals, vecs) = jacobiEigen(ata)
            var best = 0
            for (i in 1..8) if (vals[i] < vals[best]) best = i
            val hn = Homography(DoubleArray(9) { vecs[it][best] })
            // H = T_dst⁻¹ · Hn · T_src
            val res = tD.inverse().compose(hn).compose(tS)
            require(res.h.all { it.isFinite() }) { "degenerate point configuration" }
            return res
        }

        /** Symmetric eigen-decomposition (cyclic Jacobi). Returns eigenvalues and eigenvectors (columns). */
        internal fun jacobiEigen(m: Array<DoubleArray>): Pair<DoubleArray, Array<DoubleArray>> {
            val n = m.size
            val a = Array(n) { m[it].copyOf() }
            val v = Array(n) { i -> DoubleArray(n) { j -> if (i == j) 1.0 else 0.0 } }
            var scale = 0.0
            for (i in 0 until n) for (j in 0 until n) scale += a[i][j] * a[i][j]
            for (sweep in 0 until 100) {
                var off = 0.0
                for (i in 0 until n) for (j in i + 1 until n) off += a[i][j] * a[i][j]
                if (off <= 1e-30 * (scale + 1e-300)) break
                for (p in 0 until n) for (q in p + 1 until n) {
                    if (abs(a[p][q]) < 1e-300) continue
                    val theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                    val t = (if (theta >= 0) 1.0 else -1.0) / (abs(theta) + sqrt(theta * theta + 1))
                    val c = 1 / sqrt(t * t + 1)
                    val s = t * c
                    for (k in 0 until n) {
                        val akp = a[k][p]
                        val akq = a[k][q]
                        a[k][p] = c * akp - s * akq
                        a[k][q] = s * akp + c * akq
                    }
                    for (k in 0 until n) {
                        val apk = a[p][k]
                        val aqk = a[q][k]
                        a[p][k] = c * apk - s * aqk
                        a[q][k] = s * apk + c * aqk
                    }
                    for (k in 0 until n) {
                        val vkp = v[k][p]
                        val vkq = v[k][q]
                        v[k][p] = c * vkp - s * vkq
                        v[k][q] = s * vkp + c * vkq
                    }
                }
            }
            return Pair(DoubleArray(n) { a[it][it] }, v)
        }
    }
}
