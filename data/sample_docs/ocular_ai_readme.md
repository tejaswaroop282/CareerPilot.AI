# Ocular AI

Ocular AI is a deep learning system that screens retinal fundus photographs for diabetic retinopathy. It was built as a final-year project and later extended into a small web service.

## Dataset

The model is trained on the APTOS 2019 Blindness Detection dataset, which contains 3,662 labelled fundus images graded into five severity levels from no retinopathy to proliferative retinopathy. The classes are heavily imbalanced, with more than half of the images showing no disease.

## Preprocessing

Images are cleaned with Ben Graham's method: a Gaussian blur estimates the local average colour, which is subtracted to normalise lighting between cameras. Every image is then cropped to the circular retina and resized to 512 by 512 pixels. Augmentation uses random flips, rotations up to 30 degrees and brightness jitter.

## Model

The classifier is an EfficientNet-B3 backbone pretrained on ImageNet and fine-tuned end to end. Because of the class imbalance the loss is a weighted cross-entropy, with weights inversely proportional to class frequency. A regression head was tried first but ordinal classification gave better kappa.

## Training

Training used the AdamW optimiser with a learning rate of 3e-4 and a cosine decay schedule for 25 epochs. Mixed precision on a single T4 GPU brought a full run down to about 3.5 hours. Early stopping monitored validation quadratic weighted kappa.

## Results

The final model reaches a quadratic weighted kappa of 0.89 on the held-out split. On the binary task of referable versus non-referable disease it reaches 94 percent accuracy. Test-time augmentation with four flips added roughly 0.01 kappa.

## Serving

The model is exported to ONNX and served by a FastAPI endpoint at /predict using ONNX Runtime. Requests are batched in groups of 16 images, giving a p95 latency of 120 ms. The Docker image is about 1.1 GB. Grad-CAM heatmaps are returned with each prediction so clinicians can see which regions drove the decision.

## Lessons learned

Label noise between grades 1 and 2 limited the ceiling of the model; reviewing disagreements with an ophthalmologist friend showed many borderline cases. Explainability mattered more to reviewers than a further point of accuracy.
