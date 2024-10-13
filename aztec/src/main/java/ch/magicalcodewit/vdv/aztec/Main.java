package ch.magicalcodewit.vdv.aztec;

import boofcv.abst.fiducial.AztecCodePreciseDetector;
import boofcv.alg.fiducial.aztec.AztecCode;
import boofcv.alg.fiducial.qrcode.PackedBits8;
import boofcv.factory.shape.ConfigPolygonDetector;
import boofcv.factory.shape.ConfigPolygonFromContour;
import boofcv.factory.fiducial.ConfigAztecCode;
import boofcv.factory.fiducial.FactoryFiducial;
import boofcv.io.image.ConvertBufferedImage;
import javax.imageio.ImageIO;
import boofcv.struct.image.GrayU8;

import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.List;

public class Main {
    public static void main(String[] args) {
        DataInputStream in = new DataInputStream(System.in);
        BufferedImage input;
        try {
           input = ImageIO.read(in);
        } catch (IOException e) {
            System.exit(-1);
            return;
        }
        GrayU8 gray = ConvertBufferedImage.convertFrom(input, (GrayU8) null);

        ConfigAztecCode config = new ConfigAztecCode();
        AztecCodePreciseDetector<GrayU8> detector = FactoryFiducial.aztec(config, GrayU8.class);

        detector.process(gray);

        List<AztecCode> detections = detector.getDetections();

        if (detections.size() == 0) {
            System.exit(-2);
            return;
        }

        AztecCode marker = detections.get(0);
        PackedBits8 paddedBits = PackedBits8.wrap(marker.corrected, marker.messageWordCount*marker.getWordBitCount());
        PackedBits8 bits = new PackedBits8();
        if (!removeExtraBits(marker.getWordBitCount(), marker.messageWordCount, paddedBits, bits)) {
            System.exit(-3);
            return;
        }

        System.out.println(bits.length());
        for (int i = 0; i < bits.length(); i++) {
            int v = bits.get(i);
            if (v == 0) {
                System.out.print("0");
            } else {
                System.out.print("1");
            }
        }
    }

    static boolean removeExtraBits(int wordBitCount, int messageWordCount, PackedBits8 bitsExtras, PackedBits8 bits ) {
		int numWords = bitsExtras.size/wordBitCount;
		int ones = (1 << wordBitCount) - 1;
		int onesMinusOne = (1 << wordBitCount) - 2;
		for (int i = 0; i < numWords; i++) {
			int value = bitsExtras.read(i*wordBitCount, wordBitCount, true);
			if (value == 1 || value == onesMinusOne) {
				bits.append(value >> 1, wordBitCount - 1, false);
			} else if (i < messageWordCount && (value == 0 || value == ones)) {
				return false;
			} else {
				bits.append(value, wordBitCount, false);
			}
		}
		return true;
	}

	static byte[] intToByteArray(int value) {
        return new byte[] {
                (byte)(value >>> 24),
                (byte)(value >>> 16),
                (byte)(value >>> 8),
                (byte)value};
    }
}