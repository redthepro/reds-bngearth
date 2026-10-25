//! Runtime SIMD for DEM resampling. Keep the module baseline portable: only guarded functions
//! may contain AVX instructions. No fast-math/FMA: the physics lattice must keep its exact heights.
use std::sync::OnceLock;

#[derive(Clone, Copy)]
enum Backend { Scalar, Avx2, Avx512 }

fn backend() -> Backend {
    static BACKEND: OnceLock<Backend> = OnceLock::new();
    *BACKEND.get_or_init(|| {
        let requested = std::env::var("BNGE_SIMD").unwrap_or_default();
        if requested == "scalar" { return Backend::Scalar; }
        #[cfg(target_arch = "x86_64")]
        {
            let avx2 = std::is_x86_feature_detected!("avx2");
            if requested != "avx2" && avx2 && std::is_x86_feature_detected!("avx512f") {
                if requested == "avx512" { return Backend::Avx512; }
                // A wider vector is not always faster (e.g. Zen 4). Both CPU and OS
                // support have been checked before either target-feature function runs.
                return unsafe { calibrate() };
            }
            if avx2 { return Backend::Avx2; }
        }
        Backend::Scalar
    })
}

#[cfg(target_arch = "x86_64")]
unsafe fn calibrate() -> Backend {
    use std::{hint::black_box, time::Instant};
    // A bounded, warmed comparison of typical DEM work, once per process. No disk,
    // network, or repeated tuning while driving. Alternating order reduces clock/load bias.
    const SIDE: usize = 256;
    const GRID: usize = 514;
    let g: Vec<f32> = (0..GRID * GRID).map(|i| (i % 997) as f32).collect();
    let xs: Vec<f64> = (0..SIDE * SIDE).map(|i| (i % SIDE) as f64 * 513.0 / 255.0).collect();
    let ys: Vec<f64> = (0..SIDE * SIDE).map(|i| (i / SIDE) as f64 * 513.0 / 255.0).collect();
    let mut out = vec![0.0; xs.len()];
    avx2(&g, GRID, GRID, &xs, &ys, &mut out);
    avx512(&g, GRID, GRID, &xs, &ys, &mut out);
    let (mut t2, mut t512) = ([0u128; 7], [0u128; 7]);
    for round in 0..7 {
        for turn in 0..2 {
            let wide = (round + turn) % 2 == 0;
            let start = Instant::now();
            if wide { avx512(&g, GRID, GRID, &xs, &ys, &mut out); }
            else { avx2(&g, GRID, GRID, &xs, &ys, &mut out); }
            let elapsed = start.elapsed().as_nanos();
            black_box(&out);
            if wide { t512[round] = elapsed; } else { t2[round] = elapsed; }
        }
    }
    t2.sort_unstable();
    t512.sort_unstable();
    // Prefer AVX2 on close measurements: wider instructions must earn their use.
    if t512[3] * 100 < t2[3] * 97 { Backend::Avx512 } else { Backend::Avx2 }
}

pub fn name() -> &'static str {
    match backend() { Backend::Scalar => "scalar", Backend::Avx2 => "avx2", Backend::Avx512 => "avx512" }
}

#[inline]
fn scalar(g: &[f32], w: usize, h: usize, x: f64, y: f64) -> f64 {
    let ix = (x.floor() as i64).clamp(0, w as i64 - 2) as usize;
    let iy = (y.floor() as i64).clamp(0, h as i64 - 2) as usize;
    let wx = (x - ix as f64).clamp(0.0, 1.0);
    let wy = (y - iy as f64).clamp(0.0, 1.0);
    let k = iy * w + ix;
    (g[k] as f64 * (1.0 - wx) + g[k + 1] as f64 * wx) * (1.0 - wy)
        + (g[k + w] as f64 * (1.0 - wx) + g[k + w + 1] as f64 * wx) * wy
}

pub fn bilinear(g: &[f32], w: usize, h: usize, xs: &[f64], ys: &[f64]) -> Vec<f64> {
    // Keep the safe Rust entry point sound even if a future caller bypasses the
    // Python wrapper's checks. These bounds also establish safety of every gather.
    assert!(w >= 2 && h >= 2 && w.checked_mul(h) == Some(g.len()));
    assert_eq!(xs.len(), ys.len());
    let mut out = vec![0.0; xs.len()];
    let mut done = 0;
    // i32 gathers are safe only for this bound. Non-finite coordinates keep the scalar
    // conversion/clamp behavior; small calls avoid dispatch/setup overhead.
    if xs.len() >= 64 && g.len() <= i32::MAX as usize && xs.iter().chain(ys).all(|v| v.is_finite()) {
        #[cfg(target_arch = "x86_64")]
        unsafe {
            done = match backend() {
                Backend::Avx512 => avx512(g, w, h, xs, ys, &mut out),
                Backend::Avx2 => avx2(g, w, h, xs, ys, &mut out),
                Backend::Scalar => 0,
            };
        }
    }
    for k in done..xs.len() { out[k] = scalar(g, w, h, xs[k], ys[k]); }
    out
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2")]
unsafe fn avx2(g: &[f32], w: usize, h: usize, xs: &[f64], ys: &[f64], out: &mut [f64]) -> usize {
    use std::arch::x86_64::*;
    let (zero, one) = (_mm256_setzero_pd(), _mm256_set1_pd(1.0));
    let (maxx, maxy) = (_mm256_set1_pd((w - 2) as f64), _mm256_set1_pd((h - 2) as f64));
    let stride = _mm_set1_epi32(w as i32);
    let last = xs.len() / 4 * 4;
    for k in (0..last).step_by(4) {
        let (x, y) = (_mm256_loadu_pd(xs.as_ptr().add(k)), _mm256_loadu_pd(ys.as_ptr().add(k)));
        // MAX returns its second operand on a tie: indices must normalize -0 to
        // +0 just like the scalar float-to-integer conversion, including 2x2 grids.
        let ix = _mm256_min_pd(maxx, _mm256_max_pd(_mm256_floor_pd(x), zero));
        let iy = _mm256_min_pd(maxy, _mm256_max_pd(_mm256_floor_pd(y), zero));
        let wx = _mm256_min_pd(one, _mm256_max_pd(zero, _mm256_sub_pd(x, ix)));
        let wy = _mm256_min_pd(one, _mm256_max_pd(zero, _mm256_sub_pd(y, iy)));
        let i = _mm_add_epi32(_mm256_cvttpd_epi32(ix), _mm_mullo_epi32(_mm256_cvttpd_epi32(iy), stride));
        let r = _mm_add_epi32(i, _mm_set1_epi32(1));
        let b = _mm_add_epi32(i, stride);
        let br = _mm_add_epi32(r, stride);
        let a = _mm256_cvtps_pd(_mm_i32gather_ps::<4>(g.as_ptr(), i));
        let ar = _mm256_cvtps_pd(_mm_i32gather_ps::<4>(g.as_ptr(), r));
        let ab = _mm256_cvtps_pd(_mm_i32gather_ps::<4>(g.as_ptr(), b));
        let abr = _mm256_cvtps_pd(_mm_i32gather_ps::<4>(g.as_ptr(), br));
        let nx = _mm256_sub_pd(one, wx);
        let top = _mm256_add_pd(_mm256_mul_pd(a, nx), _mm256_mul_pd(ar, wx));
        let bottom = _mm256_add_pd(_mm256_mul_pd(ab, nx), _mm256_mul_pd(abr, wx));
        let value = _mm256_add_pd(_mm256_mul_pd(top, _mm256_sub_pd(one, wy)), _mm256_mul_pd(bottom, wy));
        _mm256_storeu_pd(out.as_mut_ptr().add(k), value);
    }
    last
}

#[cfg(target_arch = "x86_64")]
#[target_feature(enable = "avx2,avx512f")]
unsafe fn avx512(g: &[f32], w: usize, h: usize, xs: &[f64], ys: &[f64], out: &mut [f64]) -> usize {
    use std::arch::x86_64::*;
    let (zero, one) = (_mm512_setzero_pd(), _mm512_set1_pd(1.0));
    let (maxx, maxy) = (_mm512_set1_pd((w - 2) as f64), _mm512_set1_pd((h - 2) as f64));
    let stride = _mm256_set1_epi32(w as i32);
    let last = xs.len() / 8 * 8;
    for k in (0..last).step_by(8) {
        let (x, y) = (_mm512_loadu_pd(xs.as_ptr().add(k)), _mm512_loadu_pd(ys.as_ptr().add(k)));
        let ix = _mm512_min_pd(maxx, _mm512_max_pd(_mm512_roundscale_pd::<9>(x), zero));
        let iy = _mm512_min_pd(maxy, _mm512_max_pd(_mm512_roundscale_pd::<9>(y), zero));
        let wx = _mm512_min_pd(one, _mm512_max_pd(zero, _mm512_sub_pd(x, ix)));
        let wy = _mm512_min_pd(one, _mm512_max_pd(zero, _mm512_sub_pd(y, iy)));
        let i = _mm256_add_epi32(_mm512_cvttpd_epi32(ix), _mm256_mullo_epi32(_mm512_cvttpd_epi32(iy), stride));
        let r = _mm256_add_epi32(i, _mm256_set1_epi32(1));
        let b = _mm256_add_epi32(i, stride);
        let br = _mm256_add_epi32(r, stride);
        let a = _mm512_cvtps_pd(_mm256_i32gather_ps::<4>(g.as_ptr(), i));
        let ar = _mm512_cvtps_pd(_mm256_i32gather_ps::<4>(g.as_ptr(), r));
        let ab = _mm512_cvtps_pd(_mm256_i32gather_ps::<4>(g.as_ptr(), b));
        let abr = _mm512_cvtps_pd(_mm256_i32gather_ps::<4>(g.as_ptr(), br));
        let nx = _mm512_sub_pd(one, wx);
        let top = _mm512_add_pd(_mm512_mul_pd(a, nx), _mm512_mul_pd(ar, wx));
        let bottom = _mm512_add_pd(_mm512_mul_pd(ab, nx), _mm512_mul_pd(abr, wx));
        let value = _mm512_add_pd(_mm512_mul_pd(top, _mm512_sub_pd(one, wy)), _mm512_mul_pd(bottom, wy));
        _mm512_storeu_pd(out.as_mut_ptr().add(k), value);
    }
    last
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_mismatched_coordinates_before_gathering() {
        assert!(std::panic::catch_unwind(|| bilinear(&[0.0; 4], 2, 2, &[0.0; 64], &[])).is_err());
    }

    #[test]
    fn rejects_invalid_grid_before_gathering() {
        for (w, h) in [(1, 4), (2, 3), (usize::MAX, 2)] {
            assert!(std::panic::catch_unwind(|| bilinear(&[0.0; 4], w, h, &[0.0; 64], &[0.0; 64])).is_err());
        }
    }

    #[test]
    fn supported_kernels_preserve_scalar_bits() {
        let coords = [-0.0, 0.0, -1.0, 1.0, 1e-310, -1e-310, 1e30, -1e30, 0.1, 0.9];
        let xs: Vec<_> = (0..131).map(|i| coords[i % coords.len()]).collect();
        let ys: Vec<_> = (0..131).map(|i| coords[(i + 3) % coords.len()]).collect();
        for value in [0.0f32, -0.0, f32::from_bits(1), 32.25, f32::INFINITY, f32::NEG_INFINITY, f32::NAN] {
            let grid = [value; 4];
            let expected: Vec<_> = xs.iter().zip(&ys).map(|(&x, &y)| scalar(&grid, 2, 2, x, y)).collect();
            for mode in [Backend::Scalar, Backend::Avx2, Backend::Avx512] {
                let mut actual = vec![0.0; xs.len()];
                let done = match mode {
                    #[cfg(target_arch = "x86_64")]
                    Backend::Avx2 if std::is_x86_feature_detected!("avx2") =>
                        unsafe { avx2(&grid, 2, 2, &xs, &ys, &mut actual) },
                    #[cfg(target_arch = "x86_64")]
                    Backend::Avx512 if std::is_x86_feature_detected!("avx2") && std::is_x86_feature_detected!("avx512f") =>
                        unsafe { avx512(&grid, 2, 2, &xs, &ys, &mut actual) },
                    _ => 0,
                };
                for k in done..xs.len() { actual[k] = scalar(&grid, 2, 2, xs[k], ys[k]); }
                for (a, b) in actual.iter().zip(&expected) { assert_eq!(a.to_bits(), b.to_bits()); }
            }
        }
    }
}
