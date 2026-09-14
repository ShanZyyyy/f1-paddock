using UnityEngine;

namespace PaddockGP.Track
{
    /// <summary>
    /// game.html'deki zorunlu Bahreyn SVG pist yolunun (viewBox 0 0 1000 600)
    /// gerçek bezier çapa noktaları. Faz 0'da bu noktalar bir Catmull-Rom
    /// eğrisi için dayanak (waypoint) olarak kullanılıyor — aynı SVG'nin
    /// birebir eğrilik sadakati değil, aynı genel yerleşim/şekil. Tam
    /// eğri sadakati (kerb, kot farkı vb.) Faz 1'in konusu.
    /// </summary>
    public static class BahrainTrackData
    {
        private static readonly Vector2[] SvgAnchors =
        {
            new Vector2(180, 480), new Vector2(480, 480), new Vector2(560, 410),
            new Vector2(560, 380), new Vector2(500, 350), new Vector2(460, 365),
            new Vector2(410, 330), new Vector2(410, 240), new Vector2(500, 180),
            new Vector2(640, 180), new Vector2(720, 250), new Vector2(720, 280),
            new Vector2(660, 330), new Vector2(620, 330), new Vector2(580, 270),
            new Vector2(580, 140), new Vector2(480, 60),  new Vector2(280, 60),
            new Vector2(180, 140), new Vector2(180, 220), new Vector2(250, 280),
            new Vector2(320, 280), new Vector2(380, 350), new Vector2(380, 420),
            new Vector2(290, 480)
        };

        // SVG koordinat aralığının kabaca ortası — pisti Unity dünya
        // merkezine (0,0,0) yakın konumlandırmak için.
        private const float CenterX = 450f;
        private const float CenterY = 270f;

        /// <summary>SVG (x,y) düzlemini Unity (x, 0, z) zemin düzlemine eşler.</summary>
        public static Vector3[] GetWaypoints()
        {
            var result = new Vector3[SvgAnchors.Length];
            for (int i = 0; i < SvgAnchors.Length; i++)
            {
                var p = SvgAnchors[i];
                result[i] = new Vector3(p.x - CenterX, 0f, p.y - CenterY);
            }
            return result;
        }
    }
}
