using UnityEngine;

namespace PaddockGP.CameraRig
{
    /// <summary>
    /// Basit yörünge kamerası — Faz 0 doğrulaması için. Sağ fare tuşuyla
    /// sürükleyip döndür, tekerlekle yakınlaş/uzaklaş. Faz 3'teki yayın
    /// kamerası (lider takibi / kesme / onboard) bunun üzerine kurulacak.
    /// </summary>
    public class OrbitCamera : MonoBehaviour
    {
        [SerializeField] private Transform target;
        [SerializeField] private float distance = 25f;
        [SerializeField] private float height = 12f;
        [SerializeField] private float rotationSpeed = 120f;
        [SerializeField] private float zoomSpeed = 15f;
        [SerializeField] private float minDistance = 6f;
        [SerializeField] private float maxDistance = 80f;

        private float _yaw = 45f;

        private void LateUpdate()
        {
            if (target == null) return;

            if (Input.GetMouseButton(1))
                _yaw += Input.GetAxis("Mouse X") * rotationSpeed * Time.deltaTime;

            distance = Mathf.Clamp(distance - Input.mouseScrollDelta.y * zoomSpeed, minDistance, maxDistance);

            var rotation = Quaternion.Euler(0f, _yaw, 0f);
            var offset = rotation * new Vector3(0f, height, -distance);
            transform.position = target.position + offset;
            transform.LookAt(target.position + Vector3.up * 1.5f);
        }
    }
}
