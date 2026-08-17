"""
TF Worker — Process indépendant pour l'inférence TensorFlow.
Communication JSON via stdin/stdout.
"""
import sys
import json
import os

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TF_METAL_DEVICE_ENABLE"] = "0"
os.environ["TF_DISABLE_MKL"] = "1"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

try:
    import numpy as np
    import tensorflow as tf
    tf.config.set_visible_devices([], "GPU")
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)

    model_path = sys.argv[1]
    from tensorflow.keras.models import load_model
    model = load_model(model_path, compile=False)

    print(json.dumps({"ready": True}), flush=True)

except Exception as e:
    print(json.dumps({"ready": False, "error": str(e)}), flush=True)
    sys.exit(1)

for raw_line in sys.stdin:
    img_path = raw_line.strip()
    if not img_path or img_path == "EXIT":
        break
    try:
        img = tf.keras.utils.load_img(img_path, target_size=(224, 224))
        arr = tf.keras.utils.img_to_array(img) / 255.0
        arr = np.expand_dims(arr, axis=0)
        score = float(model.predict(arr, verbose=0)[0][0])
        confidence = int(score * 100)

        if score > 0.7:
            result = {"status": "OK",        "score": confidence, "message": "Site sain"}
        elif score > 0.5:
            result = {"status": "ATTENTION", "score": confidence, "message": "Légers problèmes détectés"}
        else:
            result = {"status": "ALERTE",    "score": confidence, "message": "Problèmes visuels majeurs"}
    except Exception as e:
        result = {"status": "ERREUR", "score": 0, "message": str(e)[:80]}

    print(json.dumps(result), flush=True)
