/** Solve A X = B (A n×n, B n×m) by Gaussian elimination with partial pivoting. */
export function solve(A: number[][], B: number[][]): number[][] {
  const n = A.length, m = B[0].length;
  const M = A.map((row, i) => [...row, ...B[i]]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r;
    if (Math.abs(M[piv][col]) < 1e-12) throw new Error("singular matrix");
    [M[col], M[piv]] = [M[piv], M[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = M[r][col] / M[col][col];
      if (f) for (let k = col; k < n + m; k++) M[r][k] -= f * M[col][k];
    }
  }
  return M.map((row, i) => row.slice(n).map((x) => x / M[i][i]));
}
