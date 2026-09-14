using UnityEngine;
using PaddockGP.Track;

namespace PaddockGP.Vehicles
{
    /// <summary>
    /// Faz 0 için yer tutucu araç hareketi: TrackSpline üzerinde sabit hızla
    /// ilerler ve yol yönüne bakar. Gerçek lastik/tempo fiziği (game.html'deki
    /// tick()/updateCar() mantığı) Faz 4'te bu script'in yerini alacak.
    /// </summary>
    public class CarFollowPath : MonoBehaviour
    {
        [SerializeField] private TrackSpline track;
        [SerializeField] private float speedMetersPerSecond = 40f;
        [SerializeField] private float heightOffset = 0.5f;

        private float _distance;

        private void Update()
        {
            if (track == null) return;

            _distance += speedMetersPerSecond * Time.deltaTime;

            var (position, forward) = track.Evaluate(_distance);
            transform.position = position + Vector3.up * heightOffset;
            if (forward.sqrMagnitude > 0.0001f)
                transform.rotation = Quaternion.LookRotation(forward, Vector3.up);
        }
    }
}
