"""
Subprocess isolé pour l'inférence TF/Keras.
Appelé par audit_engine.py via subprocess.run() pour éviter le conflit
mutex TF + Playwright sur macOS Apple Silicon.

Usage: python tf_infer.py <img_path> <model_path>
Output: JSON sur stdout — {"status": "OK"|"ATTENTION"|"ALERTE"|"ERREUR", "score": int}
"""
import sys
import json
import os

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"status": "ERREUR", "score": 0}))
        sys.exit(1)

    img_path = sys.argv[1]
    model_path = sys.argv[2]

    if not os.path.exists(img_path):
        print(json.dumps({"status": "ERREUR", "score": 0}))
        sys.exit(1)

    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

    try:
        import numpy as np
        import keras
        model = keras.models.load_model(model_path, compile=False)
        img = keras.utils.load_img(img_path, target_size=(224, 224))
        img_array = keras.utils.img_to_array(img) / 255.0
        img_array = np.expand_dims(img_array, axis=0)
        score = float(model.predict(img_array, verbose=0)[0][0])
        confidence = int(score * 100)
        if score > 0.7:
            result = {"status": "OK", "score": confidence}
        elif score > 0.5:
            result = {"status": "ATTENTION", "score": confidence}
        else:
            result = {"status": "ALERTE", "score": confidence}
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"status": "ERREUR", "score": 0, "error": str(e)}), file=sys.stderr)
        print(json.dumps({"status": "ERREUR", "score": 0}))
        sys.exit(1)

if __name__ == "__main__":
    main()
