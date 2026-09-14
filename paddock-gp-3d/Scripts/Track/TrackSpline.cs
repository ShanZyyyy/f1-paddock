using System.Collections.Generic;
using UnityEngine;

namespace PaddockGP.Track
{
    /// <summary>
    /// Kapalı bir Catmull-Rom eğrisi kurar (game.html'deki JS TRACK.pointAt()
    /// ile aynı mantık: sabit uzunluk tablosuyla mesafeden konum bulma) ve
    /// bir LineRenderer ile Play modunda görselleştirir.
    /// </summary>
    [ExecuteAlways]
    [RequireComponent(typeof(LineRenderer))]
    public class TrackSpline : MonoBehaviour
    {
        [SerializeField] private int samplesPerSegment = 20;
        [SerializeField] private float lineWidth = 8f;

        private Vector3[] _controlPoints;
        private readonly List<Vector3> _sampled = new List<Vector3>();
        private readonly List<float> _cumulativeLength = new List<float>();

        public float TotalLength { get; private set; }

        private void Awake() => Rebuild();
        private void OnValidate() => Rebuild();

        public void Rebuild()
        {
            _controlPoints = BahrainTrackData.GetWaypoints();
            SampleSpline();
            SetupLineRenderer();
        }

        private void SampleSpline()
        {
            _sampled.Clear();
            _cumulativeLength.Clear();
            int n = _controlPoints.Length;

            for (int i = 0; i < n; i++)
            {
                Vector3 p0 = _controlPoints[(i - 1 + n) % n];
                Vector3 p1 = _controlPoints[i];
                Vector3 p2 = _controlPoints[(i + 1) % n];
                Vector3 p3 = _controlPoints[(i + 2) % n];
                for (int s = 0; s < samplesPerSegment; s++)
                {
                    float t = s / (float)samplesPerSegment;
                    _sampled.Add(CatmullRom(p0, p1, p2, p3, t));
                }
            }

            TotalLength = 0f;
            _cumulativeLength.Add(0f);
            for (int i = 1; i < _sampled.Count; i++)
            {
                TotalLength += Vector3.Distance(_sampled[i], _sampled[i - 1]);
                _cumulativeLength.Add(TotalLength);
            }
        }

        private static Vector3 CatmullRom(Vector3 p0, Vector3 p1, Vector3 p2, Vector3 p3, float t)
        {
            float t2 = t * t;
            float t3 = t2 * t;
            return 0.5f * (
                2f * p1 +
                (-p0 + p2) * t +
                (2f * p0 - 5f * p1 + 4f * p2 - p3) * t2 +
                (-p0 + 3f * p1 - 3f * p2 + p3) * t3
            );
        }

        /// <summary>Pist üzerindeki mesafeden (metre) konum + ileri yön döndürür.</summary>
        public (Vector3 position, Vector3 forward) Evaluate(float distance)
        {
            if (_sampled.Count < 2) return (transform.position, Vector3.forward);

            distance = ((distance % TotalLength) + TotalLength) % TotalLength;

            int lo = 0, hi = _cumulativeLength.Count - 1;
            while (lo < hi - 1)
            {
                int mid = (lo + hi) / 2;
                if (_cumulativeLength[mid] <= distance) lo = mid; else hi = mid;
            }

            Vector3 a = _sampled[lo];
            Vector3 b = _sampled[(lo + 1) % _sampled.Count];
            float segLen = _cumulativeLength[lo + 1] - _cumulativeLength[lo];
            float segT = segLen > 0.0001f ? (distance - _cumulativeLength[lo]) / segLen : 0f;

            Vector3 pos = Vector3.Lerp(a, b, segT);
            Vector3 fwd = (b - a).sqrMagnitude > 0.0001f ? (b - a).normalized : Vector3.forward;
            return (pos, fwd);
        }

        private void SetupLineRenderer()
        {
            var lr = GetComponent<LineRenderer>();
            lr.loop = true;
            lr.useWorldSpace = true;
            lr.startWidth = lineWidth;
            lr.endWidth = lineWidth;
            lr.positionCount = _sampled.Count;
            lr.SetPositions(_sampled.ToArray());
        }
    }
}
